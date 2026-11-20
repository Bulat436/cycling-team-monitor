import { QueryCache, QueryClient, useQuery } from '@tanstack/react-query'

import { ApiError, request } from './api'
import type { Session } from './api'

export const sessionKey = ['session'] as const

export function makeQueryClient() {
  const client = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (error instanceof ApiError && error.status === 401 && query.queryKey[0] !== sessionKey[0]) {
          forgetSession(client)
        }
      },
    }),
    defaultOptions: { queries: { retry: false } },
  })
  return client
}

export function forgetSession(client: QueryClient) {
  client.removeQueries({ predicate: query => query.queryKey[0] !== sessionKey[0] })
  client.setQueryData(sessionKey, null)
}

export function useSession() {
  return useQuery({
    queryKey: sessionKey,
    queryFn: async ({ signal }) => {
      try {
        return await request<Session>('/auth/me', { signal })
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    staleTime: 30_000,
    refetchInterval: query => query.state.data ? 60_000 : false,
  })
}
