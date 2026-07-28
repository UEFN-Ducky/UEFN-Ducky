export const ARCHIVE_FOLDER_ID = "archive";
export const ARCHIVE_FOLDER_NAME = "Archive";

export function isArchiveFolderId(folderId: string): boolean {
  return folderId === ARCHIVE_FOLDER_ID;
}
