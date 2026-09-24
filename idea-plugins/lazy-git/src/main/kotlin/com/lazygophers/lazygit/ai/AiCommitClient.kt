package com.lazygophers.lazygit.ai

import com.google.gson.Gson
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

interface SseConsumer {
    /** 每个文本增量。 */
    fun onDelta(text: String)

    /** anthropic：endpoint 400 后已去掉 thinking 字段重发。 */
    fun onRetryNoThinking() {}
}

/**
 * OpenAI / Claude 双协议 SSE 流式客户端。JDK HttpClient，零第三方依赖（Gson 为平台捆绑）。
 * 调用方负责在后台线程执行（stream() 阻塞到流结束）。
 * ponytail: 单实现无重试网络退避，出错直接抛；需要时再加。
 */
class AiCommitClient(
    private val protocol: String,
    private val endpoint: String,
    private val model: String,
    private val apiKey: String,
    private val temperature: Double,
    private val connectTimeoutSeconds: Int,
) {
    private val gson = Gson()

    private val anthropic: Boolean = protocol.equals("anthropic", ignoreCase = true)

    private fun url(): String {
        val base = endpoint.trim().trimEnd('/')
        return if (anthropic) {
            if (base.endsWith("/v1/messages")) base else "$base/v1/messages"
        } else {
            if (base.endsWith("/chat/completions")) base else "$base/v1/chat/completions"
        }
    }

    private fun buildRequest(body: Map<String, Any>): HttpRequest {
        val headers = if (anthropic) {
            arrayOf("x-api-key" to apiKey, "anthropic-version" to "2023-06-01")
        } else {
            arrayOf("Authorization" to "Bearer $apiKey")
        }
        val json = gson.toJson(body)
        val b = HttpRequest.newBuilder()
            .uri(URI.create(url()))
            .timeout(Duration.ofSeconds((connectTimeoutSeconds + 120).toLong()))
            .header("Content-Type", "application/json")
            .header("Accept", "text/event-stream")
        headers.forEach { (k, v) -> b.header(k, v) }
        return b.POST(HttpRequest.BodyPublishers.ofString(json)).build()
    }

    private fun openaiBody(prompt: String): Map<String, Any> = mapOf(
        "model" to model,
        "temperature" to temperature,
        "stream" to true,
        // thinking 等价禁用：不发送任何 reasoning 字段
        "messages" to listOf(mapOf("role" to "user", "content" to prompt)),
    )

    private fun anthropicBody(prompt: String, withThinking: Boolean): Map<String, Any> {
        val body = mutableMapOf<String, Any>(
            "model" to model,
            "temperature" to temperature,
            "max_tokens" to 512,
            "stream" to true,
            "messages" to listOf(mapOf("role" to "user", "content" to prompt)),
        )
        if (withThinking) {
            // spec：新部分模型对 disabled 400，兼容在收到 400 时去掉重发
            body["thinking"] = mapOf("type" to "disabled")
        }
        return body
    }

    /** 阻塞直到流结束，返回全部文本（同时经 consumer 增量回调）。抛异常 = 失败。 */
    fun stream(prompt: String, consumer: SseConsumer): String {
        return try {
            doStream(prompt, thinkingFlag = true, consumer = consumer)
        } catch (e: Http400Exception) {
            if (anthropic) {
                consumer.onRetryNoThinking()
                doStream(prompt, thinkingFlag = false, consumer = consumer)
            } else throw e
        }
    }

    private class Http400Exception(val body: String) : RuntimeException("HTTP 400: ${body.take(300)}")

    private fun doStream(prompt: String, thinkingFlag: Boolean, consumer: SseConsumer): String {
        val body = if (anthropic) anthropicBody(prompt, withThinking = thinkingFlag) else openaiBody(prompt)
        val client = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(connectTimeoutSeconds.toLong()))
            .followRedirects(HttpClient.Redirect.NORMAL)
            .build()
        val resp: HttpResponse<java.util.stream.Stream<String>> =
            client.send(buildRequest(body), HttpResponse.BodyHandlers.ofLines())
        if (resp.statusCode() == 400) {
            val text = resp.body().collect(java.util.stream.Collectors.joining("\n"))
            throw Http400Exception(text)
        }
        if (resp.statusCode() >= 400) {
            val text = resp.body().collect(java.util.stream.Collectors.joining("\n"))
            throw RuntimeException("HTTP ${resp.statusCode()}: ${text.take(300)}")
        }
        val extract = SseParser.forProtocol(protocol)
        val sb = StringBuilder()
        resp.body().use { lines ->
            lines.forEach { line ->
                val data = SseParser.dataOf(line) ?: return@forEach
                if (SseParser.isDone(data)) return@forEach
                val delta = try { extract(data) } catch (_: Exception) { null } ?: return@forEach
                sb.append(delta)
                consumer.onDelta(delta)
            }
        }
        return sb.toString()
    }
}
