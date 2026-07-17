export const MAX_ARTIFACT_CONTENT_CHARS = 100_000;
export const MAX_ARTIFACT_TOOL_INPUT_CHARS = 700_000;

const PREVIEW_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "img-src data: blob:",
  "font-src data:",
  "media-src data: blob:",
  "connect-src 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "child-src 'none'",
  "worker-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
  "navigate-to 'none'",
].join("; ");

const SECURITY_HEAD =
  `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">` +
  '<meta name="referrer" content="no-referrer">';

export function limitArtifactContent(content: string | undefined): string {
  return (content || "").slice(0, MAX_ARTIFACT_CONTENT_CHARS);
}

/** Inject the restrictive policy before any author-provided head content. */
export function secureArtifactPreview(content: string): string {
  const bounded = limitArtifactContent(content);
  // Always place the policy before author markup. Regex-injecting into an
  // apparent <head> can be bypassed by a matching string inside a comment or
  // script; wrapping keeps the first parsed head under our control.
  return `<!doctype html><html><head>${SECURITY_HEAD}</head><body>${bounded}</body></html>`;
}
