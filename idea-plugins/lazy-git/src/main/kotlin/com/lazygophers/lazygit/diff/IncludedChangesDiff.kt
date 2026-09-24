package com.lazygophers.lazygit.diff

import com.intellij.openapi.vcs.changes.Change

/** 从 Commit 窗口传入的 includedChanges 构造给模型的最小 unified diff。 */
object IncludedChangesDiff {
    fun build(changes: Collection<Change>): String {
        if (changes.isEmpty()) return ""
        return changes.joinToString("\n") { change ->
            val before = change.beforeRevision?.content
            val after = change.afterRevision?.content
            val path = (change.afterRevision ?: change.beforeRevision)?.file?.path ?: "(unknown)"
            if (before == null || after == null) {
                "diff --git a/$path b/$path\n(binary or unavailable; file: $path)"
            } else {
                "diff --git a/$path b/$path\n--- a/$path\n+++ b/$path\n" +
                    simpleDiff(before, after)
            }
        }
    }

    private fun simpleDiff(before: String, after: String): String {
        // 保留完整内容；模型需要上下文，且无需引入 diff 库。
        return "@@\n-" + before.replace("\n", "\n-") +
            "\n+" + after.replace("\n", "\n+")
    }
}
