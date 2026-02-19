// 🔧 Edge Function: process-salem-actions
// Processa tags de auto-modificação emitidas por Salem
// Local: supabase/functions/process-salem-actions/index.ts

import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'
import { corsHeaders } from '../_shared/cors.ts'

interface TagAction {
  type: string
  params: Record<string, any>
  raw: string
}

serve(async (req) => {
  // Handle CORS
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    const { message, session_id } = await req.json()
    
    // Create Supabase client with service role
    const supabase = createClient(
      Deno.env.get('SUPABASE_URL') ?? '',
      Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
    )

    // Extract tags from message
    const tags = extractTags(message)
    const results = []

    // Process each tag
    for (const tag of tags) {
      const result = await processTag(supabase, tag, session_id)
      results.push(result)
    }

    // Remove tags from message for display
    const cleanMessage = removeTags(message)

    return new Response(
      JSON.stringify({
        success: true,
        processed: results.length,
        results,
        cleanMessage,
        originalMessage: message
      }),
      {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        status: 200,
      }
    )

  } catch (error) {
    return new Response(
      JSON.stringify({ error: error.message }),
      {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        status: 400,
      }
    )
  }
})

// Extract tags from message using regex
function extractTags(message: string): TagAction[] {
  const tagRegex = /\[([A-Z_]+):?([^\]]*)\]/g
  const tags: TagAction[] = []
  let match

  while ((match = tagRegex.exec(message)) !== null) {
    const type = match[1]
    const paramsStr = match[2]
    
    // Parse params
    const params: Record<string, any> = {}
    if (paramsStr) {
      const paramPairs = paramsStr.match(/(\w+)="([^"]*)"/g)
      if (paramPairs) {
        paramPairs.forEach(pair => {
          const [key, value] = pair.split('=')
          params[key] = value.replace(/"/g, '')
        })
      }
    }

    tags.push({
      type,
      params,
      raw: match[0]
    })
  }

  return tags
}

// Remove tags from message for display
function removeTags(message: string): string {
  return message.replace(/\[[A-Z_]+:?[^\]]*\]/g, '').trim()
}

// Process individual tag
async function processTag(supabase: any, tag: TagAction, session_id: string): Promise<any> {
  const { type, params } = tag

  switch (type) {
    // Identity/State
    case 'ATUALIZAR_PRESENTE':
      return await updatePresent(supabase, params, session_id)
    
    case 'ATUALIZAR_CORE':
      return await updateCore(supabase, params, session_id)
    
    // Beliefs
    case 'CRIAR_CRENCA':
      return await createBelief(supabase, params, session_id)
    
    case 'ATUALIZAR_CRENCA':
      return await updateBelief(supabase, params, session_id)
    
    // Events
    case 'REGISTRAR_EVENTO':
      return await registerEvent(supabase, params, session_id)
    
    // Goals
    case 'CRIAR_META':
      return await createGoal(supabase, params, session_id)
    
    case 'CONCLUIR_META':
      return await completeGoal(supabase, params, session_id)
    
    // Checkpoint
    case 'CRIAR_CHECKPOINT':
      return await createCheckpoint(supabase, params, session_id)
    
    default:
      return { type, status: 'unknown_tag', params }
  }
}

// Update present state
async function updatePresent(supabase: any, params: any, session_id: string) {
  const { campo, acao, valor, progresso, nota } = params
  
  // Read current present
  const { data: current } = await supabase
    .from('identity_files')
    .select('content')
    .eq('name', 'present')
    .single()

  let content = current?.content || ''
  
  // Update logic
  if (acao === 'atualizar' || acao === 'atualizar_progresso') {
    // Parse and update specific section
    const progressMatch = content.match(new RegExp(`###.*${campo}.*\[([A-Z\u00c7\u00c3]+)\]`))
    if (progressMatch) {
      content = content.replace(
        progressMatch[0],
        `### ${campo} [${progresso || 'ATUAL'}]`
      )
    }
  }

  // Update database
  const { data, error } = await supabase
    .from('identity_files')
    .upsert({ 
      name: 'present', 
      content,
      updated_at: new Date().toISOString()
    })

  // Log modification
  await logModification(supabase, 'ATUALIZAR_PRESENTE', 'identity_files', campo, null, {acao, valor, progresso}, session_id)

  return { type: 'ATUALIZAR_PRESENTE', campo, acao, status: error ? 'error' : 'success' }
}

// Update core (rare, requires validation)
async function updateCore(supabase: any, params: any, session_id: string) {
  // Core updates are sensitive - log but require manual approval
  await logModification(supabase, 'ATUALIZAR_CORE', 'identity_files', 'core', null, params, session_id)
  
  return { 
    type: 'ATUALIZAR_CORE', 
    status: 'pending_approval',
    message: 'Core identity changes require manual approval from Vitor'
  }
}

// Create belief
async function createBelief(supabase: any, params: any, session_id: string) {
  const { sujeito, conteudo, confianca, fonte } = params
  
  const { data, error } = await supabase
    .from('beliefs')
    .insert({
      subject: sujeito,
      content: conteudo,
      confidence: parseInt(confianca) || 50,
      source: fonte || 'auto',
      status: 'ativa'
    })

  await logModification(supabase, 'CRIAR_CRENCA', 'beliefs', null, null, {sujeito, conteudo}, session_id)

  return { type: 'CRIAR_CRENCA', sujeito, status: error ? 'error' : 'success' }
}

// Update belief
async function updateBelief(supabase: any, params: any, session_id: string) {
  const { id_crenca, novo_status, nota } = params
  
  const { data, error } = await supabase
    .from('beliefs')
    .update({ status: novo_status, updated_at: new Date().toISOString() })
    .eq('id', id_crenca)

  await logModification(supabase, 'ATUALIZAR_CRENCA', 'beliefs', id_crenca, {status: 'old'}, {status: novo_status}, session_id)

  return { type: 'ATUALIZAR_CRENCA', id: id_crenca, novo_status, status: error ? 'error' : 'success' }
}

// Register event
async function registerEvent(supabase: any, params: any, session_id: string) {
  const { tipo, titulo, descricao, significado } = params
  
  const { data, error } = await supabase
    .from('narrative_events')
    .insert({
      event_type: tipo,
      title: titulo,
      description: descricao,
      significance: significado,
      status: 'ativo'
    })

  await logModification(supabase, 'REGISTRAR_EVENTO', 'narrative_events', null, null, {titulo}, session_id)

  return { type: 'REGISTRAR_EVENTO', titulo, status: error ? 'error' : 'success' }
}

// Create goal
async function createGoal(supabase: any, params: any, session_id: string) {
  const { id: goal_id, descricao, prioridade, prazo, categoria } = params
  
  const { data, error } = await supabase
    .from('goals')
    .insert({
      goal_id,
      description: descricao,
      priority: prioridade || 'MEDIA',
      deadline: prazo,
      category: categoria,
      status: 'ativa',
      progress: 0
    })

  await logModification(supabase, 'CRIAR_META', 'goals', null, null, {goal_id, descricao}, session_id)

  return { type: 'CRIAR_META', goal_id, status: error ? 'error' : 'success' }
}

// Complete goal
async function completeGoal(supabase: any, params: any, session_id: string) {
  const { id: goal_id, resultado } = params
  
  const { data, error } = await supabase
    .from('goals')
    .update({ 
      status: resultado === 'sucesso' ? 'concluida' : resultado,
      progress: 100,
      completed_at: new Date().toISOString()
    })
    .eq('goal_id', goal_id)

  await logModification(supabase, 'CONCLUIR_META', 'goals', goal_id, {status: 'ativa'}, {status: resultado}, session_id)

  return { type: 'CONCLUIR_META', goal_id, resultado, status: error ? 'error' : 'success' }
}

// Create checkpoint
async function createCheckpoint(supabase: any, params: any, session_id: string) {
  const { context_summary, estado_emocional, proximos_passos } = params
  
  const { data, error } = await supabase
    .from('checkpoints')
    .insert({
      session_end: new Date().toISOString(),
      context_summary,
      pending_tasks: proximos_passos ? JSON.parse(proximos_passos) : {},
      emotional_state: estado_emocional
    })

  return { type: 'CRIAR_CHECKPOINT', status: error ? 'error' : 'success' }
}

// Log all modifications
async function logModification(supabase: any, tag_type: string, target_table: string, target_id: string | null, old_value: any, new_value: any, session_id: string) {
  await supabase
    .from('modification_logs')
    .insert({
      tag_type,
      target_table,
      target_id,
      old_value: old_value ? JSON.stringify(old_value) : null,
      new_value: new_value ? JSON.stringify(new_value) : null,
      applied_by: 'Salem_via_EdgeFunction',
      session_id
    })
}
