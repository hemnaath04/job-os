/**
 * Reject with a readable message when `work` outruns its budget.
 *
 * The underlying request keeps going; this only stops the UI from waiting on it
 * forever. Every call that crosses into the API container needs one of these:
 * the container scales to zero and cold-starts in about nine seconds, the proxy
 * allows up to five minutes, and a spinner with no ceiling reads as a hang.
 */
export function withTimeout<T>(
  work: Promise<T>,
  timeoutMs: number,
  message: string,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    work.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}
