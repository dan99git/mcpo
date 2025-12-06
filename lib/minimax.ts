import { LoggingService } from '../../core/logging';
import { IAIProvider, AIRequestOptions, AIResponse, AIStreamCallbacks, AIToolCall, ReasoningDetail } from './types';
import { fetchWithRetry, DEFAULT_AI_TEMPERATURE } from './utils';

export class MiniMaxProvider implements IAIProvider {
  private apiKey: string;
  private baseUrl: string;
  private logger: LoggingService;
  private reasoningSplit: boolean;

  constructor(apiKey: string, logger: LoggingService, opts?: { baseUrl?: string; reasoningSplit?: boolean }) {
    this.apiKey = apiKey;
    this.baseUrl = (opts?.baseUrl || 'https://api.minimax.io/v1').replace(/\/$/, '');
    this.logger = logger;
    this.reasoningSplit = opts?.reasoningSplit ?? true;
  }

  /**
   * Normalize content for display/logging purposes only.
   * For multimodal content, this extracts text for logging.
   * The actual multimodal content is preserved in message mapping.
   */
  private normalizeContent(content: unknown): string {
    if (!content) return '';
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content
        .map(c => {
          if (typeof c === 'string') return c;
          if (c?.type === 'text') return c.text || '';
          if (c?.type === 'image_url') return '[image]';
          return (c as any)?.text || (c as any)?.content || '';
        })
        .filter(Boolean)
        .join('\n');
    }
    if (typeof content === 'object' && content !== null && 'text' in content) {
      return (content as { text: string }).text;
    }
    return String(content);
  }

  /**
   * Prepare message content for API request.
   * Preserves multimodal content (images) in the correct format.
   */
  private prepareMessageContent(content: unknown): unknown {
    // Already in correct format (string or array of content parts)
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      // Multimodal content - pass through as-is
      // MiniMax API accepts OpenAI-compatible format:
      // [{ type: 'text', text: '...' }, { type: 'image_url', image_url: { url: '...' } }]
      return content;
    }
    // Fallback: convert to string
    return this.normalizeContent(content);
  }

  private parseMinimaxToolCalls(rawContent: string): AIToolCall[] | undefined {
    // Quick check
    if (!rawContent.includes('<minimax:tool_call>')) {
      return undefined;
    }

    const toolCalls: AIToolCall[] = [];

    // Extract tool call blocks
    const toolCallRegex = /<minimax:tool_call>(.*?)<\/minimax:tool_call>/gs;
    const invokeRegex = /<invoke name="([^"]+)">(.*?)<\/invoke>/gs;
    const paramRegex = /<parameter name="([^"]+)">(.*?)<\/parameter>/gs;

    const toolCallMatches = rawContent.matchAll(toolCallRegex);

    for (const toolCallMatch of toolCallMatches) {
      const invokeMatches = toolCallMatch[1].matchAll(invokeRegex);

      for (const invokeMatch of invokeMatches) {
        const functionName = invokeMatch[1];
        const invokeContent = invokeMatch[2];
        const parameters: Record<string, any> = {};

        const paramMatches = invokeContent.matchAll(paramRegex);
        for (const paramMatch of paramMatches) {
          const paramName = paramMatch[1];
          let paramValue = paramMatch[2].trim();

          // Try to parse as JSON for arrays/objects
          try {
            paramValue = JSON.parse(paramValue);
          } catch {
            // Keep as string if not valid JSON
          }

          parameters[paramName] = paramValue;
        }

        toolCalls.push({
          id: `call_${Date.now()}_${toolCalls.length}`,
          type: 'function',
          function: {
            name: functionName,
            arguments: JSON.stringify(parameters)
          }
        });
      }
    }

    return toolCalls.length > 0 ? toolCalls : undefined;
  }

  async chatCompletion(options: AIRequestOptions): Promise<AIResponse> {
    const url = `${this.baseUrl}/chat/completions`;

    // CRITICAL: Per MiniMax spec, must pass back full message including reasoning_details
    // for interleaved thinking to work correctly
    const body: Record<string, unknown> = {
      model: options.model,
      messages: options.messages.map(m => {
        // Use prepareMessageContent to preserve multimodal content (images)
        const msg: Record<string, unknown> = { role: m.role, content: this.prepareMessageContent(m.content) };
        // Preserve reasoning_details for interleaved thinking continuity
        if (m.reasoning_details && m.reasoning_details.length > 0) {
          msg.reasoning_details = m.reasoning_details;
        }
        // Preserve tool_calls for assistant messages
        if (m.tool_calls && m.tool_calls.length > 0) {
          msg.tool_calls = m.tool_calls;
        }
        // Preserve tool_call_id for tool result messages
        if (m.tool_call_id) {
          msg.tool_call_id = m.tool_call_id;
        }
        return msg;
      }),
      temperature: options.temperature ?? DEFAULT_AI_TEMPERATURE,
      // CRITICAL: reasoning_split must be top-level for raw HTTP (not inside extra_body which is SDK convention)
      reasoning_split: this.reasoningSplit
    };

    if (options.max_tokens) body.max_tokens = options.max_tokens;
    if (options.tools && options.tools.length > 0) body.tools = options.tools;

    this.logger.debug('[MiniMax] Request', {
      model: options.model,
      messageCount: options.messages.length,
      url
    });

    const response = await fetchWithRetry(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => response.statusText);
      this.logger.error('[MiniMax] API error', { status: response.status, statusText: response.statusText, error: errorText });
      throw new Error(`MiniMax API error: ${response.status} ${response.statusText}`);
    }

    const data = await response.json() as any;
    const choice = data.choices?.[0];
    const rawContent = this.normalizeContent(choice?.message?.content);
    
    // CRITICAL: Preserve FULL reasoning_details array for interleaved thinking
    // Per MiniMax spec: "the entire response_message including reasoning_details MUST be
    // preserved in message history and passed back to the model"
    const reasoningDetails: ReasoningDetail[] | undefined = choice?.message?.reasoning_details;
    const reasoning = reasoningDetails?.[0]?.text || undefined;

    // Use native tool_calls from response if available (preferred)
    let toolCalls = choice?.message?.tool_calls as AIToolCall[] | undefined;
    
    // Fallback: Parse XML tool calls from content (legacy format)
    if (!toolCalls || toolCalls.length === 0) {
      toolCalls = this.parseMinimaxToolCalls(rawContent);
    }

    // Strip XML tags from content if tool calls were found via XML parsing
    let cleanContent = rawContent;
    if (toolCalls && rawContent.includes('<minimax:tool_call>')) {
      cleanContent = rawContent.replace(/<minimax:tool_call>.*?<\/minimax:tool_call>/gs, '').trim();
    }

    this.logger.debug('[MiniMax] Response', {
      hasContent: !!cleanContent,
      hasToolCalls: !!toolCalls?.length,
      hasReasoningDetails: !!reasoningDetails?.length,
      reasoningDetailsCount: reasoningDetails?.length || 0
    });

    return {
      content: cleanContent,
      tool_calls: toolCalls,
      usage: data.usage,
      reasoning: reasoning,
      // CRITICAL: Return full reasoning_details array for orchestrator to preserve
      reasoning_details: reasoningDetails
    };
  }

  async streamChatCompletion(options: AIRequestOptions, callbacks: AIStreamCallbacks): Promise<AIResponse> {
    const url = `${this.baseUrl}/chat/completions`;

    // CRITICAL: Per MiniMax spec, must pass back full message including reasoning_details
    const body: Record<string, unknown> = {
      model: options.model,
      messages: options.messages.map(m => {
        // Use prepareMessageContent to preserve multimodal content (images)
        const msg: Record<string, unknown> = { role: m.role, content: this.prepareMessageContent(m.content) };
        if (m.reasoning_details && m.reasoning_details.length > 0) {
          msg.reasoning_details = m.reasoning_details;
        }
        if (m.tool_calls && m.tool_calls.length > 0) {
          msg.tool_calls = m.tool_calls;
        }
        if (m.tool_call_id) {
          msg.tool_call_id = m.tool_call_id;
        }
        return msg;
      }),
      temperature: options.temperature ?? DEFAULT_AI_TEMPERATURE,
      stream: true,
      // CRITICAL: reasoning_split must be top-level for raw HTTP (not inside extra_body which is SDK convention)
      reasoning_split: this.reasoningSplit
    };

    if (options.max_tokens) body.max_tokens = options.max_tokens;
    if (options.tools && options.tools.length > 0) body.tools = options.tools;

    const response = await fetchWithRetry(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => response.statusText);
      this.logger.error('[MiniMax] Stream API error', { status: response.status, error: errorText });
      throw new Error(`MiniMax API error: ${response.status} ${response.statusText}`);
    }

    const streamBody = response.body;
    if (!streamBody || typeof streamBody.getReader !== 'function') {
      throw new Error('Response body is not a readable stream');
    }

    const reader = streamBody.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let accumulatedContent = '';
    let accumulatedReasoning = '';
    const accumulatedReasoningDetails: ReasoningDetail[] = [];
    let accumulatedToolCalls: AIToolCall[] = [];

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) continue;

          const data = trimmed.slice(6);
          if (data === '[DONE]') break;

          try {
            const chunk = JSON.parse(data);
            if (chunk.error) throw new Error(chunk.error.message || 'Stream error');

            const delta = chunk.choices?.[0]?.delta;
            if (!delta) continue;

            // Handle reasoning_details - accumulate FULL array for interleaved thinking
            if (delta.reasoning_details && Array.isArray(delta.reasoning_details)) {
              for (const rd of delta.reasoning_details) {
                const reasoningText = rd.text || '';
                accumulatedReasoning += reasoningText;
                
                // Find or create the reasoning detail entry
                const existingIdx = accumulatedReasoningDetails.findIndex(
                  d => d.id === rd.id || d.index === rd.index
                );
                if (existingIdx >= 0) {
                  // Append text to existing entry
                  accumulatedReasoningDetails[existingIdx] = {
                    ...accumulatedReasoningDetails[existingIdx],
                    text: (accumulatedReasoningDetails[existingIdx].text || '') + reasoningText
                  };
                } else {
                  // Add new entry
                  accumulatedReasoningDetails.push({
                    type: rd.type || 'reasoning.text',
                    id: rd.id,
                    format: rd.format,
                    index: rd.index ?? accumulatedReasoningDetails.length,
                    text: reasoningText
                  });
                }
              }
              // Emit reasoning callback
              if (callbacks.onReasoning) {
                callbacks.onReasoning(delta.reasoning_details[0]?.text || '');
              }
              if (callbacks.onReasoningDetails) {
                callbacks.onReasoningDetails(delta.reasoning_details);
              }
            }

            // Handle native tool_calls from stream
            if (delta.tool_calls && Array.isArray(delta.tool_calls)) {
              for (const tc of delta.tool_calls) {
                const idx = tc.index ?? accumulatedToolCalls.length;
                if (!accumulatedToolCalls[idx]) {
                  accumulatedToolCalls[idx] = {
                    id: tc.id || `call_${Date.now()}_${idx}`,
                    type: 'function',
                    function: { name: '', arguments: '' }
                  };
                }
                if (tc.id) accumulatedToolCalls[idx].id = tc.id;
                if (tc.function?.name) accumulatedToolCalls[idx].function.name += tc.function.name;
                if (tc.function?.arguments) accumulatedToolCalls[idx].function.arguments += tc.function.arguments;
              }
            }

            // Handle content
            if (delta.content) {
              accumulatedContent += delta.content;
              if (callbacks.onToken) callbacks.onToken(delta.content);
            }
          } catch (e) {
            // Ignore parse errors for partial chunks
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    // Use native tool_calls if accumulated, otherwise parse XML fallback
    let toolCalls: AIToolCall[] | undefined = accumulatedToolCalls.length > 0 
      ? accumulatedToolCalls.filter(tc => tc.function.name) 
      : undefined;
    
    // Fallback: Parse XML tool calls from accumulated content (legacy format)
    if (!toolCalls || toolCalls.length === 0) {
      toolCalls = this.parseMinimaxToolCalls(accumulatedContent);
    }

    // Strip XML tags if tool calls found via XML parsing
    let cleanContent = accumulatedContent;
    if (toolCalls && accumulatedContent.includes('<minimax:tool_call>')) {
      cleanContent = accumulatedContent.replace(/<minimax:tool_call>.*?<\/minimax:tool_call>/gs, '').trim();
    }
    
    if (toolCalls && toolCalls.length > 0 && callbacks.onToolCalls) {
      callbacks.onToolCalls(toolCalls);
    }

    // Build final reasoning_details array
    const finalReasoningDetails: ReasoningDetail[] | undefined = 
      accumulatedReasoningDetails.length > 0 ? accumulatedReasoningDetails : undefined;

    this.logger.debug('[MiniMax] Stream complete', {
      hasContent: !!cleanContent,
      hasToolCalls: !!toolCalls?.length,
      hasReasoningDetails: !!finalReasoningDetails?.length,
      reasoningDetailsCount: finalReasoningDetails?.length || 0
    });

    return {
      content: cleanContent,
      tool_calls: toolCalls,
      reasoning: accumulatedReasoning || undefined,
      // CRITICAL: Return full reasoning_details for orchestrator to preserve in message history
      reasoning_details: finalReasoningDetails
    };
  }
}
