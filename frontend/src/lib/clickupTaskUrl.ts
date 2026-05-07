export function clickupTaskUrl(taskId: string | null | undefined): string | null {
  if (!taskId || !String(taskId).trim()) return null
  const id = String(taskId).trim()
  return `https://app.clickup.com/t/${id}`
}
