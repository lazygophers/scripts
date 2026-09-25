package com.lazygophers.lazygit.ai

import com.google.gson.JsonParser
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * AiCommitClient 的功能测试：用 JDK 自带的 HttpServer 当假端点，不碰真 API。
 *
 * 覆盖 URL 归一化、两套协议的鉴权头、SSE 累积，以及 anthropic 的
 * 「400 就去掉 thinking 重发」这条兼容分支。
 */
class AiCommitClientTest {

    private lateinit var server: HttpServer
    private val requests = mutableListOf<Recorded>()

    /** 一次请求的记录：路径、请求头、请求体。 */
    data class Recorded(val path: String, val headers: Map<String, String>, val body: String)

    /** 每次请求返回什么，由具体用例塞进来。 */
    private var responder: (Int, HttpExchange) -> Unit = { _, _ -> }

    @BeforeTest
    fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            val body = exchange.requestBody.readBytes().toString(StandardCharsets.UTF_8)
            val headers = exchange.requestHeaders.entries.associate { it.key.lowercase() to it.value.first() }
            requests.add(Recorded(exchange.requestURI.path, headers, body))
            responder(requests.size - 1, exchange)
        }
        server.start()
    }

    @AfterTest
    fun stop() {
        server.stop(0)
    }

    private fun base(): String = "http://127.0.0.1:${server.address.port}"

    private fun sse(exchange: HttpExchange, vararg lines: String) {
        val payload = (lines.joinToString("\n") + "\n").toByteArray(StandardCharsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "text/event-stream")
        exchange.sendResponseHeaders(200, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    private fun fail(exchange: HttpExchange, status: Int, text: String) {
        val payload = text.toByteArray(StandardCharsets.UTF_8)
        exchange.sendResponseHeaders(status, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    private class Collector : SseConsumer {
        val deltas = mutableListOf<String>()
        var retriedWithoutThinking = false
        override fun onDelta(text: String) {
            deltas.add(text)
        }

        override fun onRetryNoThinking() {
            retriedWithoutThinking = true
        }
    }

    private fun client(protocol: String, endpoint: String = base()) = AiCommitClient(
        protocol = protocol,
        endpoint = endpoint,
        model = "test-model",
        apiKey = "secret-key",
        temperature = 0.2,
        connectTimeoutSeconds = 5,
    )

    @Test
    fun `openai stream accumulates deltas and ignores DONE`() {
        responder = { _, exchange ->
            sse(
                exchange,
                """data: {"choices":[{"delta":{"content":"feat"}}]}""",
                "",
                """data: {"choices":[{"delta":{"content":": 加登录"}}]}""",
                "data: [DONE]",
            )
        }
        val collector = Collector()
        val text = client("openai").stream("prompt", collector)

        assertEquals("feat: 加登录", text)
        assertEquals(listOf("feat", ": 加登录"), collector.deltas)
        assertEquals("/v1/chat/completions", requests.single().path)
        assertEquals("Bearer secret-key", requests.single().headers["authorization"])
        assertEquals("text/event-stream", requests.single().headers["accept"])
    }

    @Test
    fun `openai endpoint already ending in chat completions is not doubled`() {
        responder = { _, exchange -> sse(exchange, "data: [DONE]") }
        client("openai", "${base()}/v1/chat/completions/").stream("p", Collector())
        assertEquals("/v1/chat/completions", requests.single().path)
    }

    @Test
    fun `openai body carries model temperature stream and no thinking field`() {
        responder = { _, exchange -> sse(exchange, "data: [DONE]") }
        client("openai").stream("写个提交信息", Collector())

        val body = JsonParser.parseString(requests.single().body).asJsonObject
        assertEquals("test-model", body.get("model").asString)
        assertEquals(0.2, body.get("temperature").asDouble)
        assertTrue(body.get("stream").asBoolean)
        assertNull(body.get("thinking"))
        assertEquals("写个提交信息", body.getAsJsonArray("messages").get(0).asJsonObject.get("content").asString)
    }

    @Test
    fun `anthropic uses its own headers path and thinking disabled`() {
        responder = { _, exchange ->
            sse(
                exchange,
                """data: {"type":"message_start"}""",
                """data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"fix"}}""",
                """data: {"type":"content_block_delta","delta":{"type":"text_delta","text":": 空指针"}}""",
                """data: {"type":"message_stop"}""",
            )
        }
        val collector = Collector()
        val text = client("anthropic").stream("prompt", collector)

        assertEquals("fix: 空指针", text)
        val request = requests.single()
        assertEquals("/v1/messages", request.path)
        assertEquals("secret-key", request.headers["x-api-key"])
        assertEquals("2023-06-01", request.headers["anthropic-version"])
        val body = JsonParser.parseString(request.body).asJsonObject
        assertEquals("disabled", body.getAsJsonObject("thinking").get("type").asString)
        assertEquals(512, body.get("max_tokens").asInt)
    }

    @Test
    fun `anthropic retries without thinking after a 400`() {
        responder = { index, exchange ->
            if (index == 0) {
                fail(exchange, 400, """{"error":"thinking disabled is not supported"}""")
            } else {
                sse(exchange, """data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}""")
            }
        }
        val collector = Collector()
        val text = client("anthropic").stream("prompt", collector)

        assertEquals("ok", text)
        assertTrue(collector.retriedWithoutThinking, "400 之后应通知调用方已去掉 thinking 重发")
        assertEquals(2, requests.size)
        assertTrue(JsonParser.parseString(requests[0].body).asJsonObject.has("thinking"))
        assertNull(JsonParser.parseString(requests[1].body).asJsonObject.get("thinking"))
    }

    @Test
    fun `openai does not retry on 400`() {
        responder = { _, exchange -> fail(exchange, 400, "bad request") }
        assertFailsWith<RuntimeException> { client("openai").stream("prompt", Collector()) }
        assertEquals(1, requests.size)
    }

    @Test
    fun `a 500 surfaces as an exception carrying the body`() {
        responder = { _, exchange -> fail(exchange, 500, "upstream exploded") }
        val error = assertFailsWith<RuntimeException> { client("openai").stream("prompt", Collector()) }
        assertTrue(error.message!!.contains("500"))
        assertTrue(error.message!!.contains("upstream exploded"))
    }

    @Test
    fun `malformed sse payloads are skipped instead of killing the stream`() {
        responder = { _, exchange ->
            sse(
                exchange,
                "data: not-json",
                """data: {"choices":[]}""",
                """data: {"choices":[{"delta":{}}]}""",
                """data: {"choices":[{"delta":{"content":"kept"}}]}""",
            )
        }
        assertEquals("kept", client("openai").stream("prompt", Collector()))
    }
}
