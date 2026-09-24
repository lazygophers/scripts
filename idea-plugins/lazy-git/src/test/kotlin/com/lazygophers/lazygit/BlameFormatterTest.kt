package com.lazygophers.lazygit

import java.util.Date
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class BlameFormatterTest {
    private val date = Date(1758643200000L) // 2025-09-23 UTC

    @Test
    fun `formats author time and first message line`() {
        val blame = BlameFormatter.format("张三", date, "feat: 新增功能\n\n正文第二段")
        assertEquals("张三", blame?.author)
        assertEquals("feat: 新增功能", blame?.message)
        assertEquals(" 张三 · ${java.text.SimpleDateFormat("yyyy-MM-dd").format(date)} · feat: 新增功能", blame?.display())
    }

    @Test
    fun `trims whitespace around fields`() {
        val blame = BlameFormatter.format("  bob ", date, "  fix: trim  ")
        assertEquals("bob", blame?.author)
        assertEquals("fix: trim", blame?.message)
    }

    @Test
    fun `blank author is hidden`() {
        assertNull(BlameFormatter.format("   ", date, "msg"))
    }

    @Test
    fun `null date uncommitted line is hidden`() {
        assertNull(BlameFormatter.format("bob", null, "msg"))
    }

    @Test
    fun `blank message is hidden`() {
        assertNull(BlameFormatter.format("bob", date, "  \n \n"))
    }
}
