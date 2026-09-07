// Mirrors the JSON shape returned by GET /jobs/{id}/status (see gui/jobs.py Job).
export interface JobStatus {
  id: string;
  status: "pending" | "running" | "succeeded" | "failed";
  lines: string[];
  error: string | null;
}

// Mirrors the JSON shape returned by GET /deployments/check-domain.
export interface DomainCheckResult {
  auto: boolean;
}

// Mirrors the JSON shape returned by GET /deployments/{domain}/logs/status.
export interface RunnerStatusResult {
  status: { health: string; git_ref: string; git_commit: string } | null;
  error: string | null;
  admin_credentials: { email: string; password: string } | null;
  admin_credentials_error: string | null;
  containers: Record<string, string[]> | null;
  containers_error: string | null;
}

// Mirrors the JSON shape returned by GET /deployments/{domain}/logs/containers/{container}/tail.
export interface ContainerTailResult {
  lines: string[] | null;
  error: string | null;
}
