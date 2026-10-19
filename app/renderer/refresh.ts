// Concurrent callers share work, but a newer terminal event must not be
// satisfied by a snapshot captured before that event.
export class SnapshotRefresh<
  T extends { sequence: number; instance?: string },
> {
  private jobs = new Map<string, { minimum: number; promise: Promise<T> }>();
  run(
    key: string,
    minimum: number,
    read: () => Promise<T>,
    instance?: string,
  ): Promise<T> {
    const existing = this.jobs.get(key);
    if (existing) {
      existing.minimum = Math.max(existing.minimum, minimum);
      return existing.promise;
    }
    const job = { minimum, promise: null as unknown as Promise<T> };
    this.jobs.set(key, job);
    job.promise = Promise.resolve().then(async () => {
      try {
        let result: T;
        do {
          result = await read();
        } while (
          result.sequence < job.minimum &&
          (!instance || result.instance === instance)
        );
        return result;
      } finally {
        if (this.jobs.get(key) === job) this.jobs.delete(key);
      }
    });
    return job.promise;
  }
}
