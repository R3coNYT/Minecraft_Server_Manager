import { useOutletContext } from 'react-router-dom'
import type { Server, ServerStatus } from '@/lib/types'

export interface ServerContext {
  server: Server
  status: ServerStatus | null
}

/** Contexte fourni par `ServerLayout` aux pages d'un serveur. */
export function useServerContext(): ServerContext {
  return useOutletContext<ServerContext>()
}
