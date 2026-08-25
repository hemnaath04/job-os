import { toast } from "sonner";
import { failureDescription } from "./error-recovery";

/**
 * Report a failed action as an instruction.
 *
 * The headline names what did not happen, so the reader knows the scope
 * without reading further. The body (see failureDescription in
 * error-recovery.ts) leads with the backend's own detail text when it has
 * one, and only falls back to a generic, pattern-matched guess when there is
 * no real detail to show.
 *
 * `action` completes "Couldn't ...", so pass a bare verb phrase: "save your
 * preferences", "archive that application".
 */
export function reportFailure(action: string, error: unknown, recovery?: string) {
  toast.error(`Couldn't ${action}`, {
    description: failureDescription(error, recovery),
  });
}
