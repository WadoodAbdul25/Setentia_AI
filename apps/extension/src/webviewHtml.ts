export function prepareWebviewHtml(
  html: string,
  baseUri: string,
  cspSource: string,
  nonce: string,
): string {
  return html
    .replaceAll("./assets/", `${baseUri}/assets/`)
    .replace(
      "<head>",
      `<head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src ${cspSource} 'unsafe-inline'; script-src ${cspSource} 'nonce-${nonce}'; font-src ${cspSource};">`,
    )
    .replaceAll("<script ", `<script nonce="${nonce}" `);
}
