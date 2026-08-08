/** Fragment share codec: zlib-deflated base64url JSON, carried in the URL
 * fragment only. Fragments are never sent to the server; encoding and
 * decoding happen entirely client-side. */

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text: string): Uint8Array {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

async function pipe(bytes: Uint8Array, transform: GenericTransformStream): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(transform);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function encodeShare(value: unknown): Promise<string> {
  const json = new TextEncoder().encode(JSON.stringify(value));
  const deflated = await pipe(json, new CompressionStream("deflate"));
  return toBase64Url(deflated);
}

export async function decodeShare(fragment: string): Promise<unknown> {
  const inflated = await pipe(
    fromBase64Url(fragment),
    new DecompressionStream("deflate"),
  );
  return JSON.parse(new TextDecoder().decode(inflated));
}
