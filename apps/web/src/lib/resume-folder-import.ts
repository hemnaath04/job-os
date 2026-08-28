/**
 * Which files a "choose a folder" resume import should actually take, and
 * which one of them is the master.
 *
 * The Resumes page used to filter a picked folder with
 * `/^Hemnaath_Balasubramani_/i`, the name of the one person who built this.
 * For every other signed-in account that matched nothing: the folder picker
 * opened, the user chose a folder, and the page did nothing at all -- no
 * import, no toast, no error, no way to tell whether it had failed or was
 * still working. A dead button with a person's name compiled into it.
 *
 * The honest rule is about the file, not about who owns it: a folder import
 * takes the resume-shaped files it finds. macOS and editors litter folders
 * with dotfiles, and a picked folder is recursive, so those are dropped -- not
 * because of who they belong to, but because they are not resumes.
 */

/** Extensions the import pipeline can actually read. */
const RESUME_EXTENSIONS = /\.(pdf|docx|json)$/i;

/**
 * `webkitdirectory` reports every descendant, so a picked folder can carry
 * `.DS_Store`, `._resume.pdf` (AppleDouble sidecars, which are not PDFs
 * despite the extension) and similar. Matching on the basename, since the
 * reported name may be a relative path.
 */
function isHiddenFile(name: string): boolean {
  const base = name.split("/").pop() ?? name;
  return base.startsWith(".");
}

/** Every file in the picked folder the importer can read, hidden ones dropped. */
export function pickResumeFiles<T extends { name: string }>(files: T[]): T[] {
  return files.filter((file) => !isHiddenFile(file.name) && RESUME_EXTENSIONS.test(file.name));
}

/**
 * The file that becomes the protected master, by name.
 *
 * A resume folder normally holds one file that says "master" somewhere in its
 * name, whatever else surrounds it. When several do, the first in the picked
 * order wins rather than the import guessing between them. When none does,
 * this returns undefined and the whole folder imports as ordinary source
 * resumes: silently promoting an arbitrary file to the protected master a
 * user did not name would be worse than leaving them to set one.
 */
export function findMasterFilename<T extends { name: string }>(
  files: T[],
): string | undefined {
  return files.find((file) => /master/i.test(file.name))?.name;
}
