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

async function errorText(res: Response): Promise<string> {
  const detail = await jsonField(res, 'detail')
  return typeof detail === 'string' ? detail : `HTTP ${res.status}`
}

export async function fetchDocuments(): Promise<SourceDocument[]> {
  const res = await authFetch('/documents')
  // A 401 here means renewal failed temporarily; the session is still active.
  if (res.status === 401) throw new Error('Could not refresh your login. Please try again.')
  if (!res.ok) throw new Error(await errorText(res))
  const data = await readJson(res)
  if (!Array.isArray(data)) throw new Error('Unexpected response from the API')
  return data as SourceDocument[]
}
