import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { blobToBase64 } from "../src/base64.ts";

describe("blobToBase64", () => {
  it("encodes an empty blob to an empty string", async () => {
    assert.equal(await blobToBase64(new Blob([])), "");
  });

  it("matches Buffer's base64 for text", async () => {
    const text = "hello 世界";
    const encoded = await blobToBase64(new Blob([text]));
    assert.equal(encoded, Buffer.from(text, "utf8").toString("base64"));
  });

  it("keeps every byte across the 0x8000 chunk boundary", async () => {
    // 截图/录屏的 blob 远大于一个 chunk；分块拼接写错会在边界处丢字节
    const bytes = new Uint8Array(0x8000 * 2 + 17);
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = i % 256;
    const encoded = await blobToBase64(new Blob([bytes]));
    assert.equal(encoded, Buffer.from(bytes).toString("base64"));
    assert.deepEqual(new Uint8Array(Buffer.from(encoded, "base64")), bytes);
  });

  it("handles bytes that are not valid UTF-8 (binary PNG data)", async () => {
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0xff, 0xfe]);
    assert.equal(await blobToBase64(new Blob([bytes])), Buffer.from(bytes).toString("base64"));
  });
});
