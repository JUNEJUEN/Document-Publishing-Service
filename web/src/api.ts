import { jsonField, readJson } from './http'
import { authFetch } from './session'

export interface SourceDocument {
  id: string
  filename: string
  contentType: string
  size: number | null
  deleted: boolean
  status: string
  statusMessage: string | null
  archiveUri: string | null
  archiveSha256: string | null
}

export type JobState = 'queued' | 'running' | 'done' | 'failed'

export interface Job {
  id: string
  requestId: string
  documentId: string
  state: JobState
  error: string | null
  createdAt: string
  updatedAt: string
  archivePath: string | null
  vendorAttempts: number
  vendorReceipt: string | null
  canRetry: boolean
}

class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function errorText(res: Response): Promise<string> {
  const detail = await jsonField(res, 'detail')
  return typeof detail === 'string' ? detail : `HTTP ${res.status}`
}

/** The JSON body of a successful call; otherwise throws a message for the user. */
async function callApi(path: string, init?: RequestInit): Promise<unknown> {
  const res = await authFetch(path, init)
  // A 401 here means renewal failed temporarily; the session is still active.
  if (res.status === 401) throw new Error('Could not refresh your login. Please try again.')
  if (!res.ok) throw new ApiError(res.status, await errorText(res))
  return readJson(res)
}

function asList<T>(data: unknown): T[] {
  if (!Array.isArray(data)) throw new Error('Unexpected response from the API')
  return data as T[]
}

function newRequestId(): string {
  // randomUUID exists only in secure contexts (https or localhost).
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export async function fetchDocuments(): Promise<SourceDocument[]> {
  return asList<SourceDocument>(await callApi('/documents'))
}

export async function fetchJobs(): Promise<Job[]> {
  return asList<Job>(await callApi('/jobs'))
}

export async function publish(documentId: string): Promise<void> {
  try {
    await callApi('/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ documentId, requestId: newRequestId() }),
    })
  } catch (e) {
    // FastAPI's 422 lists the invalid fields; the only typed one is the id.
    if (e instanceof ApiError && e.status === 422) {
      throw new Error('Invalid document id. Use letters, digits, ".", "_" or "-".')
    }
    throw e
  }
}

export async function retryJob(jobId: string): Promise<void> {
  await callApi(`/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' })
}
