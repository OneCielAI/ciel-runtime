// Probe only: drives the unmodified product side-intake.js startSide() so its
// TextEncoder + crypto.subtle digest runs inside the engine's hooks host.
import { startSide, sideMcpParity } from './side-intake.js'

export const register = (on) => {
  on('command.run', { command: 'parity-probe' }, async ($) => {
    const globals = {
      TextEncoder: typeof TextEncoder,
      subtleDigest: typeof globalThis.crypto?.subtle?.digest,
      process: typeof globalThis.process,
      require: typeof globalThis.require,
    }
    await startSide($)
    return { text: JSON.stringify({ globals, parity: sideMcpParity() }) }
  })
}
