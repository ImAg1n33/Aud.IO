/**
 * SSE Parser — independent state machine that survives chunk boundaries.
 *
 * Standard SSE wire format:
 *   event: <type>\n
 *   data: <payload>\n
 *   \n
 *
 * Multi-line data (RFC-compliant):
 *   data: line1\n
 *   data: line2\n
 *   \n
 *
 * This parser buffers partial blocks across feed() calls so that events
 * split by network / Nginx / mobile chunks are never lost.
 */

export class SSEParser {
  constructor() {
    this._buffer = ''
  }

  /**
   * Feed a raw text chunk.  Returns zero or more complete {event, data}
   * objects parsed from the accumulated buffer.
   */
  feed(chunk) {
    const events = []
    this._buffer += chunk

    while (true) {
      const idx = this._buffer.indexOf('\n\n')
      if (idx === -1) break

      const block = this._buffer.slice(0, idx)
      this._buffer = this._buffer.slice(idx + 2)

      let eventType = ''
      const dataLines = []

      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          dataLines.push(line.slice(6))
        }
      }

      if (eventType) {
        events.push({ event: eventType, data: dataLines.join('\n') })
      }
    }

    return events
  }

  /** Discard all buffered state (call before a new request). */
  reset() {
    this._buffer = ''
  }
}
