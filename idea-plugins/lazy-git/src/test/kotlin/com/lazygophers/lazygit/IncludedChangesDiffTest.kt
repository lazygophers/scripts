package com.lazygophers.lazygit.diff

import com.intellij.openapi.vcs.FilePath
import com.intellij.openapi.vcs.LocalFilePath
import com.intellij.openapi.vcs.changes.Change
import com.intellij.openapi.vcs.changes.ContentRevision
import com.intellij.openapi.vcs.history.VcsRevisionNumber
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** IncludedChangesDiff：喂给模型的 unified diff 文本，纯字符串拼装，无需 IDE fixture。 */
class IncludedChangesDiffTest {

    private class FakeRevision(private val path: FilePath, private val text: String?) : ContentRevision {
        override fun getContent(): String? = text
        override fun getFile(): FilePath = path
        override fun getRevisionNumber(): VcsRevisionNumber = VcsRevisionNumber.NULL
    }

    private fun path(p: String): FilePath = LocalFilePath(p, false)

    private fun change(file: String, before: String?, after: String?): Change {
        val fp = path(file)
        return Change(
            before?.let { FakeRevision(fp, it) },
            after?.let { FakeRevision(fp, it) },
        )
    }

    @Test
    fun `no changes yields an empty diff`() {
        assertEquals("", IncludedChangesDiff.build(emptyList()))
    }

    @Test
    fun `a modified file keeps both sides in full`() {
        val diff = IncludedChangesDiff.build(listOf(change("src/a.kt", "old\nline", "new\nline")))
        assertEquals(
            "diff --git a/src/a.kt b/src/a.kt\n--- a/src/a.kt\n+++ b/src/a.kt\n" +
                "@@\n-old\n-line\n+new\n+line",
            diff,
        )
    }

    @Test
    fun `an added file has no before revision and is still reported`() {
        val diff = IncludedChangesDiff.build(listOf(change("src/new.kt", null, "hello")))
        assertTrue(diff.startsWith("diff --git a/src/new.kt b/src/new.kt"))
        assertTrue(diff.contains("binary or unavailable"))
    }

    @Test
    fun `a deleted file falls back to the before path`() {
        val diff = IncludedChangesDiff.build(listOf(change("src/gone.kt", "bye", null)))
        assertTrue(diff.contains("src/gone.kt"))
        assertTrue(diff.contains("binary or unavailable"))
    }

    @Test
    fun `several changes are joined one block per file`() {
        val diff = IncludedChangesDiff.build(
            listOf(change("a.txt", "1", "2"), change("b.txt", "3", "4")),
        )
        assertEquals(2, Regex("diff --git").findAll(diff).count())
        assertTrue(diff.contains("a/a.txt"))
        assertTrue(diff.contains("a/b.txt"))
    }
}
