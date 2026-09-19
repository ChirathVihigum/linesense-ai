/** Only same-app absolute paths may be used as the post-login target (the server re-checks). */
export function safeNextPath(value: string | null): string {
  if (!value?.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return '/'
  if (value.startsWith('/login')) return '/'
  return value
}
