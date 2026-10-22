/** Execution-time authority and draining for hosted relay commands. */
import type { State } from "../shared/engine.js";

type Operation<T> = (guard: () => void, signal: AbortSignal) => Promise<T>;

export function commandAuthority(current: () => State) {
  let epoch = 0, fencing = 0;
  const running = new Set<{ abort: AbortController; finished: Promise<void> }>();
  return {
    capture(command: any, actor: string) {
      // Capture at receipt, before a command can wait behind a slow login.
      const issued = epoch;
      return async <T>(operation: Operation<T>): Promise<T> => {
        const abort = new AbortController();
        const guard = () => {
          if (fencing || issued !== epoch || abort.signal.aborted) throw new Error("stale_control");
          const state = current();
          if (command.deadline_at && Date.now() / 1000 >= command.deadline_at) throw new Error("run_deadline");
          if (Date.now() / 1000 >= state.expires_at) throw new Error("session_expired");
          if (command.generation !== state.control_generation) throw new Error("stale_control");
          if (!["observe", "highlight", "pages"].includes(command.action) && state.controller !== actor)
            throw new Error("control_required");
        };
        guard();
        let finish!: () => void;
        const entry = { abort, finished: new Promise<void>(resolve => { finish = resolve; }) };
        running.add(entry);
        try {
          const result = await operation(guard, abort.signal);
          guard();
          return result;
        } finally {
          running.delete(entry);
          finish();
        }
      };
    },
    async fence() {
      epoch += 1;
      fencing += 1;
      const active = [...running];
      for (const entry of active) entry.abort.abort(new Error("stale_control"));
      try { await Promise.all(active.map(entry => entry.finished)); }
      finally { fencing -= 1; }
    },
  };
}
