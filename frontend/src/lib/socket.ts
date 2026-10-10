import { initSocket } from 'frappe-ui'
import type { Socket } from 'socket.io-client'

// One realtime connection for the whole app; the agent publishes its progress on it.
let socket: Socket | null = null

export function getSocket(): Socket {
  if (!socket) socket = initSocket()
  return socket
}
