package com.lazygophers.lazygit.ai

import com.google.gson.JsonParser

/** 纯函数：SSE 行 → 文本增量。无网络，可单测。 */
object SseParser {

    /** 提取一行 SSE 的 data 负载；非 data 行或 [DONE] 返回 null（[DONE] 用 isDone 判）。 */
    fun dataOf(line: String): String? {
        val t = line.trim()
        if (!t.startsWith("data:")) return null
        return t.removePrefix("data:").trim().ifEmpty { null }
    }

    fun isDone(data: String): Boolean = data == "[DONE]"

    /** openai Chat Completions：choices[0].delta.content */
    fun openaiDelta(json: String): String? {
        val root = JsonParser.parseString(json).asJsonObject ?: return null
        val choices = root.getAsJsonArray("choices") ?: return null
        if (choices.size() == 0) return null
        val delta = choices.get(0).asJsonObject.getAsJsonObject("delta") ?: return null
        val c = delta.get("content") ?: return null
        if (c.isJsonNull) return null
        return c.asString
    }

    /** anthropic Messages：type=content_block_delta 时 delta.text */
    fun anthropicDelta(json: String): String? {
        val root = JsonParser.parseString(json).asJsonObject ?: return null
        if (root.get("type")?.takeIf { !it.isJsonNull }?.asString != "content_block_delta") return null
        val delta = root.getAsJsonObject("delta") ?: return null
        if (delta.get("type")?.asString != "text_delta") return null
        val t = delta.get("text") ?: return null
        if (t.isJsonNull) return null
        return t.asString
    }

    /** 按协议选 extractor。 */
    fun forProtocol(protocol: String): (String) -> String? =
        if (protocol.equals("anthropic", ignoreCase = true)) ::anthropicDelta else ::openaiDelta
}
