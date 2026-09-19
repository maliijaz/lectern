/** Live job progress over server-sent events. */

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { JobEvent, JobStatus } from "./types";

export interface JobProgress {
  status: JobStatus | "idle";
  progress: number;
  message: string;
  error: string;
  result: Record<string, unknown> | null;
  done: boolean;
}

const IDLE: JobProgress = {
  status: "idle",
  progress: 0,
  message: "",
  error: "",
  result: null,
  done: false,
};

const TERMINAL: JobStatus[] = ["succeeded", "failed", "cancelled"];

/**
 * Follow a job until it finishes.
 *
 * The stream sends a snapshot first, so reopening the page mid-job shows the right state
 * rather than starting the bar at zero. If the connection drops — a laptop lid closing
 * during a long generation — it falls back to polling rather than silently stalling.
 */
export function useJob(jobId: string | null, onDone?: (event: JobProgress) => void): JobProgress {
  const [state, setState] = useState<JobProgress>(IDLE);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (!jobId) {
      setState(IDLE);
      return;
    }

    setState({ ...IDLE, status: "queued" });

    let source: EventSource | null = null;
    let pollTimer: number | undefined;
    let cancelled = false;

    const finish = (next: JobProgress) => {
      if (cancelled) return;
      setState(next);
      if (next.done) {
        source?.close();
        window.clearInterval(pollTimer);
        doneRef.current?.(next);
      }
    };

    const applyEvent = (event: JobEvent) => {
      if (event.type === "ping") return;
      const status = (event.status ?? "running") as JobStatus;
      finish({
        status,
        progress: event.progress ?? 0,
        message: event.message ?? "",
        error: event.error ?? "",
        result: (event.result as Record<string, unknown>) ?? null,
        done: TERMINAL.includes(status),
      });
    };

    const startPolling = () => {
      window.clearInterval(pollTimer);
      pollTimer = window.setInterval(async () => {
        try {
          const job = await api.job(jobId);
          finish({
            status: job.status,
            progress: job.progress,
            message: job.message,
            error: job.error,
            result: job.result,
            done: TERMINAL.includes(job.status),
          });
        } catch {
          /* keep polling — a transient failure should not end the watch */
        }
      }, 1500);
    };

    try {
      source = new EventSource(api.jobStreamUrl(jobId));
      source.onmessage = (message) => {
        try {
          applyEvent(JSON.parse(message.data) as JobEvent);
        } catch {
          /* ignore an unparseable frame */
        }
      };
      source.onerror = () => {
        // EventSource retries on its own, but a closed stream means the job may have
        // finished while we were disconnected; polling catches that.
        startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      source?.close();
      window.clearInterval(pollTimer);
    };
  }, [jobId]);

  return state;
}
