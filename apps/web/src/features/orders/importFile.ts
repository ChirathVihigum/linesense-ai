/** Same limit as the backend (`MAX_FILE_BYTES` in app/domain/orders/import_csv.py). */
export const MAX_IMPORT_BYTES = 1_048_576

export function checkImportFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.csv')) return 'Choose a .csv file.'
  if (file.size > MAX_IMPORT_BYTES) return 'The file is larger than 1 MB.'
  if (file.size === 0) return 'The file is empty.'
  return null
}
