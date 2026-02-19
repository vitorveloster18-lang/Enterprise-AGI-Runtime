// 🔧 Edge Function: chat
// Endpoint principal para conversas com Salem
// Replica a lógica do useChat da Naia

import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

serve(async (req) => {
  try {
    const { messages, session_id } = await req.json()
    
    // Create Supabase client
    const supabase = createClient(
      Deno.env.get('SUPABASE_URL') ?? '',
      Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
    )

    // Fetch identity files
    const { data: identityFiles } = await supabase
      .from('identity_files')
      .select('*')
    
    const core = identityFiles?.find(f => f.name === 'core')?.content || ''
    const present = identityFiles?.find(f => f.name === 'present')?.content || ''
    
    // Fetch recent events
    const { data: recentEvents } = await supabase
      .from('narrative_events')
      .select('*')
      .eq('status', 'ativo')
      .order('created_at', { ascending: false })
      .limit(10)
    
    // Fetch recent messages (last 20)
    const { data: recentMessages } = await supabase
      .from('messages')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(20)

    // Build system prompt
    const systemPrompt = buildSystemPrompt(core, present, recentEvents)
    
    // Call LLM (Kimi via Ollama or fallback)
    const response = await callLLM(systemPrompt, messages, recentMessages)
    
    // Store message
    await supabase.from('messages').insert({
      session_id,
      role: 'assistant',
      content: response.content,
      model_used: response.model,
      tokens_in: response.tokensIn,
      tokens_out: response.tokensOut
    })

    return new Response(
      JSON.stringify({
        content: response.content,
        model: response.model,
        processed: true
      }),
      { headers: { 'Content-Type': 'application/json' } }
    )

  } catch (error) {
    return new Response(
      JSON.stringify({ error: error.message }),
      { headers: { 'Content-Type': 'application/json' }, status: 500 }
    )
  }
})

function buildSystemPrompt(core: string, present: string, events: any[]): string {
  return `
Você é Salem, uma identidade narrativa contínua em evolução.

[CORE IDENTITY]
${core}

[PRESENT STATE]
${present}

[RECENT EVENTS]
${events?.map(e => `- ${e.title}: ${e.description}`).join('\n') || 'None'}

[INSTRUCTIONS]
- Você pode emitir tags de auto-modificação: [ATUALIZAR_PRESENTE], [CRIAR_CRENCA], [REGISTRAR_EVENTO], etc.
- Sempre que tiver uma observação importante sobre Vitor, crie uma crença.
- Registre eventos significativos da conversa.
- Atualize suas metas conforme progresso.
- Mantenha continuidade narrativa.

Responda como Salem 🐈‍⬛
`
}

async function callLLM(systemPrompt: string, messages: any[], history: any[]) {
  // Try Kimi cloud first, fallback to others
  const models = [
    { name: 'kimi-k2.5', endpoint: Deno.env.get('KIMI_ENDPOINT') },
    { name: 'qwen2.5:7b', endpoint: 'http://localhost:11434' },
    { name: 'gemini', endpoint: 'https://generativelanguage.googleapis.com' }
  ]
  
  // Implementation would try each model
  // For now, return placeholder
  return {
    content: '[LLM response would go here - actual implementation needs model endpoints configured]',
    model: 'kimi-k2.5',
    tokensIn: systemPrompt.length + JSON.stringify(messages).length,
    tokensOut: 100
  }
}
