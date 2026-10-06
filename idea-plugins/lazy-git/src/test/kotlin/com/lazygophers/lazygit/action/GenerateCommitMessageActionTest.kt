package com.lazygophers.lazygit.action

import com.intellij.notification.Notification
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.actionSystem.ActionUiKind
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.actionSystem.DataContext
import com.intellij.openapi.actionSystem.Presentation
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.vcs.FilePath
import com.intellij.openapi.vcs.LocalFilePath
import com.intellij.openapi.vcs.VcsDataKeys
import com.intellij.openapi.vcs.changes.Change
import com.intellij.openapi.vcs.changes.ContentRevision
import com.intellij.openapi.vcs.history.VcsRevisionNumber
import com.lazygophers.lazygit.FakeIde
import com.lazygophers.lazygit.FakeIndicator
import com.lazygophers.lazygit.fakeProject
import com.lazygophers.lazygit.settings.LazyGitSettings
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * GenerateCommitMessageAction 的分支逻辑：update 的可用性开关、actionPerformed 的
 * 各个提前返回分支、AI 后台任务的正常/取消/失败路径，以及 writeMessage 的反射兼容。
 * Project/CommitWorkflowUi/通知/设置页全部用接口代理，AI 走本地 HttpServer。
 * SwingUtilities.invokeLater 排队进真 EDT，测试不等待其执行（成功写入路径依赖 EDT）。
 */
class GenerateCommitMessageActionTest {

    private lateinit var server: HttpServer
    private var respond: (HttpExchange) -> Unit = { }

    @BeforeTest
    fun start() {
        FakeIde.install()
        FakeIde.settings.loadState(LazyGitSettings.State())
        FakeIde.store.clear()
        FakeIde.progressManager.ranTasks.clear()
        FakeIde.progressManager.overrideIndicator(null)
        FakeIde.notifications.clear()
        // 真实 NotificationGroup 实例（final class，有公开构造器）；通知捕获在 fakeProject
        // 的 messageBus 代理里做（Notification.notify(project) 走 Notifications 主题）
        val group = com.intellij.notification.NotificationGroup(
            "Lazy Git", com.intellij.notification.NotificationDisplayType.BALLOON, false, null, null
        )
        FakeIde.registerService(
            NotificationGroupManager::class.java,
            com.lazygophers.lazygit.interfaceProxy(NotificationGroupManager::class.java) { _, _ -> group }
        )
        // ShowSettingsUtil 是抽象类（不可 Proxy），空实现全部方法：main 代码只调
        // showSettingsDialog(project, "Lazy Git")，这里记录调用次数
        FakeIde.registerService(ShowSettingsUtil::class.java, com.lazygophers.lazygit.FakeShowSettingsUtil())
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            respond(exchange)
        }
        server.start()
    }

    @AfterTest
    fun stop() {
        server.stop(0)
    }

    private fun sse(exchange: HttpExchange, vararg lines: String) {
        val payload = (lines.joinToString("\n") + "\n").toByteArray(StandardCharsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "text/event-stream")
        exchange.sendResponseHeaders(200, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    private fun fail500(exchange: HttpExchange) {
        val payload = "boom".toByteArray(StandardCharsets.UTF_8)
        exchange.sendResponseHeaders(500, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    /** 事件的数据源：按 DataKey 名字应答。 */
    private class MapDataContext(val map: Map<String, Any?>) : DataContext {
        override fun getData(dataId: String): Any? = map[dataId]
    }

    private fun event(data: Map<String, Any?>): AnActionEvent =
        AnActionEvent.createEvent(
            MapDataContext(data), Presentation(), "", ActionUiKind.NONE, null
        )

    private fun uiProxy(included: List<Change>, displayed: List<Change> = included): Any =
        com.lazygophers.lazygit.interfaceProxy(
            com.intellij.vcs.commit.CommitWorkflowUi::class.java
        ) { method, _ ->
            when (method.name) {
                "getIncludedChanges" -> included
                "getDisplayedChanges" -> displayed
                else -> null
            }
        }

    private fun action() = GenerateCommitMessageAction()

    private fun change(file: String, before: String, after: String): Change {
        val fp: FilePath = LocalFilePath(file, false)
        fun rev(text: String): ContentRevision = object : ContentRevision {
            override fun getContent(): String = text
            override fun getFile(): FilePath = fp
            override fun getRevisionNumber(): VcsRevisionNumber = VcsRevisionNumber.NULL
        }
        return Change(rev(before), rev(after))
    }

    private fun aiReady(endpoint: String = "http://127.0.0.1:${server.address.port}") {
        FakeIde.settings.loadState(LazyGitSettings.State(endpoint = endpoint, model = "m"))
        FakeIde.passwordSafe.setPassword(LazyGitSettings.apiKeyAttributes(), "k")
    }

    // ---- update ----

    @Test
    fun `update disables the presentation without a commit workflow ui`() {
        val e = event(mapOf(VcsDataKeys.COMMIT_WORKFLOW_UI.name to null))
        action().update(e)
        assertTrue(!e.presentation.isEnabled)
    }

    @Test
    fun `update enables the presentation when a commit workflow ui is present`() {
        val e = event(mapOf(VcsDataKeys.COMMIT_WORKFLOW_UI.name to uiProxy(emptyList())))
        action().update(e)
        assertTrue(e.presentation.isEnabled)
    }

    // ---- actionPerformed：提前返回分支 ----

    @Test
    fun `actionPerformed does nothing without a project`() {
        aiReady()
        action().actionPerformed(event(mapOf(VcsDataKeys.COMMIT_WORKFLOW_UI.name to uiProxy(emptyList()))))
        assertEquals(0, FakeIde.progressManager.ranTasks.size)
    }

    @Test
    fun `actionPerformed points at the settings page when ai is not configured`() {
        // 默认 state endpoint/model 为空 → isAiReady false
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to uiProxy(emptyList()),
        )))
        assertEquals(1, FakeIde.notifications.size)
        assertEquals(NotificationType.WARNING, FakeIde.notifications.single().second)
        assertEquals(0, FakeIde.progressManager.ranTasks.size)
    }

    @Test
    fun `actionPerformed does nothing without a commit workflow ui`() {
        aiReady()
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to null,
        )))
        assertEquals(0, FakeIde.progressManager.ranTasks.size)
    }

    @Test
    fun `actionPerformed warns when the diff is blank`() {
        aiReady()
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to uiProxy(emptyList()),
        )))
        assertEquals(1, FakeIde.notifications.size)
        assertEquals(NotificationType.WARNING, FakeIde.notifications.single().second)
        assertEquals(0, FakeIde.progressManager.ranTasks.size)
    }

    // ---- actionPerformed：AI 任务后台执行 ----

    @Test
    fun `actionPerformed runs the ai task and the model answers`() {
        aiReady()
        respond = { exchange ->
            sse(exchange, """data: {"choices":[{"delta":{"content":"feat: add"}}]}""", "data: [DONE]")
        }
        val ui = uiProxy(listOf(change("a.kt", "old", "new")))
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to ui,
        )))

        assertEquals(1, FakeIde.progressManager.ranTasks.size)
    }

    @Test
    fun `the ai task swallows a cancellation raised by a canceled indicator`() {
        aiReady()
        FakeIde.progressManager.overrideIndicator(FakeIndicator(canceled = true))
        respond = { exchange ->
            sse(exchange, """data: {"choices":[{"delta":{"content":"x"}}]}""")
        }
        val ui = uiProxy(listOf(change("a.kt", "old", "new")))
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to ui,
        )))
        // 取消路径静默返回，无错误通知
        assertTrue(FakeIde.notifications.none { it.second == NotificationType.ERROR })
    }

    @Test
    fun `the ai task retries anthropic without thinking after a 400`() {
        FakeIde.settings.loadState(LazyGitSettings.State(
            protocol = "anthropic", endpoint = "http://127.0.0.1:${server.address.port}", model = "m"))
        FakeIde.passwordSafe.setPassword(LazyGitSettings.apiKeyAttributes(), "k")
        var calls = 0
        respond = { exchange ->
            if (calls++ == 0) fail500style(exchange, 400) else sse(exchange, """data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}""", "data: [DONE]")
        }
        val ui = uiProxy(listOf(change("a.kt", "old", "new")))
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to ui,
        )))
        assertEquals(1, FakeIde.progressManager.ranTasks.size)
    }

    private fun fail500style(exchange: HttpExchange, status: Int) {
        val payload = """{"error":"thinking disabled"}""".toByteArray(StandardCharsets.UTF_8)
        exchange.sendResponseHeaders(status, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    @Test
    fun `the ai task reports an http failure through a notification`() {
        aiReady()
        respond = { exchange -> fail500(exchange) }
        val ui = uiProxy(listOf(change("a.kt", "old", "new")))
        action().actionPerformed(event(mapOf(
            CommonDataKeys.PROJECT.name to fakeProject(),
            VcsDataKeys.COMMIT_WORKFLOW_UI.name to ui,
        )))
        assertEquals(1, FakeIde.progressManager.ranTasks.size)
    }

    // ---- writeMessage 反射兼容 ----

    private fun callWriteMessage(ui: Any, message: String): Boolean {
        val m = GenerateCommitMessageAction::class.java.getDeclaredMethod("writeMessage", Any::class.java, String::class.java)
        m.isAccessible = true
        return m.invoke(action(), ui, message) as Boolean
    }

    @Test
    fun `writeMessage uses a direct setCommitMessage method when present`() {
        var received: String? = null
        val ui = com.lazygophers.lazygit.interfaceProxy(
            com.intellij.vcs.commit.CommitWorkflowUi::class.java, WithSetCommitMessage::class.java
        ) { method, args ->
            when (method.name) {
                "setCommitMessage" -> { received = args?.get(0) as? String; true; }
                else -> null
            }
        }
        assertTrue(callWriteMessage(ui, "msg"))
        assertEquals("msg", received)
    }

    /** writeMessage 反射找 setCommitMessage(String)，proxy 上得真有这个方法 */
    interface WithSetCommitMessage {
        fun setCommitMessage(message: String): Boolean
    }

    interface WithGetCommitMessage {
        fun getCommitMessage(): Any?
    }

    @Test
    fun `writeMessage falls back to a getter and its target setter`() {
        // 真 JTextField：setText(String) 存在，headless 下可构造
        val editor = javax.swing.JTextField()
        val ui = com.lazygophers.lazygit.interfaceProxy(
            com.intellij.vcs.commit.CommitWorkflowUi::class.java, WithGetCommitMessage::class.java
        ) { method, _ ->
            when (method.name) {
                "getCommitMessage" -> editor; else -> null
            }
        }
        assertTrue(callWriteMessage(ui, "msg"))
    }

    @Test
    fun `writeMessage returns false when no accessor matches`() {
        val ui = com.lazygophers.lazygit.interfaceProxy(
            com.intellij.vcs.commit.CommitWorkflowUi::class.java
        ) { _, _ -> null }
        assertTrue(!callWriteMessage(ui, "msg"))
    }
}
