package com.lazygophers.lazygit.ai

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class SseParserTest {
    @Test
    fun `parses openai delta and done`() {
        assertEquals("feat", SseParser.openaiDelta("""{"choices":[{"delta":{"content":"feat"}}]}"""))
        assertEquals("feat", SseParser.dataOf("data: {\"x\":1}" )?.let { "feat" })
        assertEquals("[DONE]", SseParser.dataOf("data: [DONE]"))
    }

    @Test
    fun `parses anthropic text delta and ignores thinking`() {
        assertEquals("hello", SseParser.anthropicDelta("""{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}"""))
        assertNull(SseParser.anthropicDelta("""{"type":"message_start"}"""))
    }
}
