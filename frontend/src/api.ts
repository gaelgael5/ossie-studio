export interface VolumeStatus {
  name: string
  path: string
  writable: boolean
  detail: string | null
}

export interface DbStatus {
  reachable: boolean
  schema_version: number | null
  expected_version: number
  up_to_date: boolean
  detail: string | null
}

export interface HealthReport {
  ok: boolean
  database: DbStatus
  volumes: VolumeStatus[]
}

export async function fetchHealth(): Promise<HealthReport> {
  // 503 porte un corps JSON exploitable : on ne jette pas sur un statut non-200.
  const response = await fetch('/api/health')
  const body = (await response.json()) as HealthReport
  return body
}
