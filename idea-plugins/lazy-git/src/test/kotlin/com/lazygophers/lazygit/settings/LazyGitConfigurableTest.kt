package com.lazygophers.lazygit.settings

import com.lazygophers.lazygit.FakeIde
import com.sun.net.httpserver.HttpServer
import java.lang.reflect.Field
import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import javax.swing.JComponent
import javax.swing.JPasswordField
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * LazyGitConfigurable 的设置页逻辑：createComponent / apply / reset / isModified /
 * currentSettings / runTestConnection。Application 用 FakeIde 假体，
 * AI 连接用 JDK HttpServer 假端点（与 AiCommitClientTest 同一模式）。
 * Swing 面板构造与字段同步是页面的全部真实逻辑，逐项断言。
 */
class LazyGitConfigurableTest {

    private lateinit var server: HttpServer
    private var failWith500: Boolean = false

    @BeforeTest
    fun start() {
        FakeIde.install()
        failWith500 = false
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            val status = if (failWith500) 500 else 200
            val payload = if (failWith500) "boom".toByteArray() else
                """data: {"choices":[{"delta":{"content":"ok"}}]}
data: [DONE]
""".toByteArray(StandardCharsets.UTF_8)
            exchange.responseHeaders.add("Content-Type", "text/event-stream")
            exchange.sendResponseHeaders(status, payload.size.toLong())
            exchange.responseBody.use { it.write(payload) }
        }
        server.start()
    }

    @AfterTest
    fun stop() {
        server.stop(0)
    }

    // ---- 反射助手：Configurable 的字段全是 private Swing 组件 ----

    private fun field(instance: Any, name: String): Any {
        var c: Class<*> = instance.javaClass
        while (true) {
            try {
                val f: Field = c.getDeclaredField(name)
                f.isAccessible = true
                return f.get(instance)
            } catch (_: NoSuchFieldException) {
                c = c.superclass ?: error("no field $name")
            }
        }
    }

    private fun page(): LazyGitConfigurable = LazyGitConfigurable()

    private fun fillUi(p: LazyGitConfigurable, endpoint: String, model: String, key: String) {
        (field(p, "endpoint") as javax.swing.JTextField).text = endpoint
        (field(p, "model") as javax.swing.JTextField).text = model
        (field(p, "apiKey") as JPasswordField).text = key
    }

    // ---- 用例 ----

    @Test
    fun `createComponent builds the panel and the display name is set`() {
        val p = page()
        val component = p.createComponent()
        assertTrue(component is JComponent && component.componentCount >= 8)
        assertEquals("Lazy Git", p.displayName)
    }

    @Test
    fun `reset loads state and stored key into the fields`() {
        FakeIde.settings.loadState(
            LazyGitSettings.State(protocol = "anthropic", endpoint = "https://e", model = "m",
                temperature = 0.5, connectTimeoutSeconds = 9, language = "en"))
        FakeIde.store.clear()
        FakeIde.passwordSafe.setPassword(LazyGitSettings.apiKeyAttributes(), "stored-key")

        val p = page()
        p.reset()

        assertEquals("anthropic", (field(p, "protocol") as javax.swing.JComboBox<*>).selectedItem)
        assertEquals("https://e", (field(p, "endpoint") as javax.swing.JTextField).text)
        assertEquals("m", (field(p, "model") as javax.swing.JTextField).text)
        assertEquals("0.5", (field(p, "temperature") as javax.swing.JTextField).text)
        assertEquals("9", (field(p, "timeout") as javax.swing.JTextField).text)
        assertEquals(1, (field(p, "language") as javax.swing.JComboBox<*>).selectedIndex)
        assertEquals("stored-key", String((field(p, "apiKey") as JPasswordField).password))
    }

    @Test
    fun `apply writes ui values into the settings and the key into the store`() {
        val p = page()
        fillUi(p, endpoint = "https://api.example.test", model = "m1", key = "k1")
        (field(p, "temperature") as javax.swing.JTextField).text = "0.9"
        (field(p, "timeout") as javax.swing.JTextField).text = "30"

        p.apply()

        assertEquals("https://api.example.test", FakeIde.settings.api.endpoint)
        assertEquals("m1", FakeIde.settings.api.model)
        assertEquals(0.9, FakeIde.settings.api.temperature)
        assertEquals(30, FakeIde.settings.api.connectTimeoutSeconds)
        assertEquals("k1", FakeIde.passwordSafe.getPassword(LazyGitSettings.apiKeyAttributes()))
    }

    @Test
    fun `apply falls back to defaults for malformed numbers`() {
        val p = page()
        fillUi(p, endpoint = "https://e", model = "m", key = "k")
        (field(p, "temperature") as javax.swing.JTextField).text = "abc"
        (field(p, "timeout") as javax.swing.JTextField).text = ""

        p.apply()

        assertEquals(0.3, FakeIde.settings.api.temperature)
        assertEquals(15, FakeIde.settings.api.connectTimeoutSeconds)
    }

    @Test
    fun `isModified is true when ui differs from stored state`() {
        FakeIde.settings.loadState(LazyGitSettings.State(endpoint = "stored", model = "m"))
        FakeIde.store.clear()
        val p = page()
        p.reset()
        fillUi(p, endpoint = "changed", model = "m", key = "")

        assertTrue(p.isModified)
    }

    @Test
    fun `isModified is true when only the key differs`() {
        FakeIde.settings.loadState(LazyGitSettings.State(endpoint = "e", model = "m"))
        FakeIde.store.clear()
        val p = page()
        p.reset()
        (field(p, "apiKey") as JPasswordField).text = "different-key"

        assertTrue(p.isModified)
    }

    @Test
    fun `isModified is false right after a reset`() {
        FakeIde.settings.loadState(LazyGitSettings.State(endpoint = "e", model = "m", language = "en"))
        FakeIde.store.clear()
        FakeIde.passwordSafe.setPassword(LazyGitSettings.apiKeyAttributes(), "same")
        val p = page()
        p.reset()

        assertFalse(p.isModified)
    }

    @Test
    fun `test connection fails fast when endpoint model or key is blank`() {
        val p = page()
        val method = LazyGitConfigurable::class.java.getDeclaredMethod("runTestConnection")
        method.isAccessible = true

        fillUi(p, endpoint = " ", model = "", key = "")
        assertEquals(false, method.invoke(p))
    }

    @Test
    fun `test connection returns true when the endpoint answers with text`() {
        val p = page()
        val method = LazyGitConfigurable::class.java.getDeclaredMethod("runTestConnection")
        method.isAccessible = true
        fillUi(p, endpoint = "http://127.0.0.1:${server.address.port}", model = "m", key = "k")

        assertEquals(true, method.invoke(p))
    }

    @Test
    fun `test connection retries anthropic without thinking after a 400`() {
        var calls = 0
        server.removeContext("/")
        server.createContext("/") { exchange ->
            val status = if (calls++ == 0) 400 else 200
            val payload = if (status == 400) """{"error":"thinking disabled"}""".toByteArray() else
                "data: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"ok\"}}\ndata: [DONE]\n".toByteArray(StandardCharsets.UTF_8)
            exchange.responseHeaders.add("Content-Type", "text/event-stream")
            exchange.sendResponseHeaders(status, payload.size.toLong())
            exchange.responseBody.use { it.write(payload) }
        }
        val p = page()
        val method = LazyGitConfigurable::class.java.getDeclaredMethod("runTestConnection")
        method.isAccessible = true
        (field(p, "protocol") as javax.swing.JComboBox<*>).selectedIndex = 1
        fillUi(p, endpoint = "http://127.0.0.1:${server.address.port}", model = "m", key = "k")

        assertEquals(true, method.invoke(p))
    }

    @Test
    fun `the test connection button runs the check and updates the label`() {
        val p = page()
        fillUi(p, endpoint = "http://127.0.0.1:${server.address.port}", model = "m", key = "k")
        // 面板里唯一的 JButton 就是「测试连接」
        var button: javax.swing.JButton? = null
        fun walk(c: java.awt.Component) {
            when (c) {
                is javax.swing.JButton -> button = c
                is java.awt.Container -> c.components.forEach { walk(it) }
            }
        }
        walk(p.createComponent())
        button!!.doClick()
        // 后台线程跑完才会 invokeLater 更新 label；真 EDT 会消费它，轮询到为止
        var tries = 0
        val label = field(p, "testResult") as javax.swing.JLabel
        while (!label.text.contains("测试连接") && tries < 200) { Thread.sleep(10); tries++ }
        assertTrue(label.text.contains("成功") || label.text.contains("失败"))
    }

    @Test
    fun `test connection surfaces the endpoint error to the button callback`() {
        failWith500 = true
        val p = page()
        val method = LazyGitConfigurable::class.java.getDeclaredMethod("runTestConnection")
        method.isAccessible = true
        fillUi(p, endpoint = "http://127.0.0.1:${server.address.port}", model = "m", key = "k")

        // stream 抛异常，runTestConnection 会把它抛给调用方（按钮回调负责兜住）
        try {
            method.invoke(p)
            error("expected an exception")
        } catch (_: java.lang.reflect.InvocationTargetException) {
            // 预期路径
        }
    }
}
