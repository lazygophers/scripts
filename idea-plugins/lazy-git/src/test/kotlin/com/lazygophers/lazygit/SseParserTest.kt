package com.lazygophers.lazygit.ai

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SseParserTest {

    @Test
    fun `dataOf keeps only data lines`() {
        assertEquals("""{"x":1}""", SseParser.dataOf("""data: {"x":1}"""))
        assertEquals("""{"x":1}""", SseParser.dataOf("""data:{"x":1}"""))
        assertEquals("[DONE]", SseParser.dataOf("data: [DONE]"))
        assertNull(SseParser.dataOf("event: message"))
        assertNull(SseParser.dataOf(""))
        assertNull(SseParser.dataOf("data:"))
        assertNull(SseParser.dataOf("data:   "))
        assertNull(SseParser.dataOf(": keep-alive comment"))
    }

    @Test
    fun `isDone only matches the sentinel`() {
        assertTrue(SseParser.isDone("[DONE]"))
        assertFalse(SseParser.isDone("[done]"))
        assertFalse(SseParser.isDone("""{"choices":[]}"""))
    }

    @Test
    fun `openaiDelta reads choices zero delta content`() {
        assertEquals("feat", SseParser.openaiDelta("""{"choices":[{"delta":{"content":"feat"}}]}"""))
        assertEquals("", SseParser.openaiDelta("""{"choices":[{"delta":{"content":""}}]}"""))
    }

    @Test
    fun `openaiDelta returns null for every shape without content`() {
        assertNull(SseParser.openaiDelta("""{"choices":[]}"""))
        assertNull(SseParser.openaiDelta("""{"choices":[{"delta":{}}]}"""))
        assertNull(SseParser.openaiDelta("""{"choices":[{"delta":{"content":null}}]}"""))
        assertNull(SseParser.openaiDelta("""{"id":"x"}"""))
    }

    @Test
    fun `anthropicDelta only accepts a content_block_delta of type text_delta`() {
        assertEquals(
            "hello",
            SseParser.anthropicDelta("""{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}"""),
        )
        assertNull(SseParser.anthropicDelta("""{"type":"message_start"}"""))
        assertNull(SseParser.anthropicDelta("""{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"…"}}"""))
        assertNull(SseParser.anthropicDelta("""{"type":"content_block_delta","delta":{"type":"text_delta","text":null}}"""))
    }

    @Test
    fun `forProtocol picks the extractor and defaults to openai`() {
        val anthropic = SseParser.forProtocol("Anthropic")
        val openai = SseParser.forProtocol("anything-else")
        val anthropicPayload = """{"type":"content_block_delta","delta":{"type":"text_delta","text":"a"}}"""
        val openaiPayload = """{"choices":[{"delta":{"content":"o"}}]}"""

        assertEquals("a", anthropic(anthropicPayload))
        assertNull(anthropic(openaiPayload))
        assertEquals("o", openai(openaiPayload))
        assertNull(openai(anthropicPayload))
    }
}
