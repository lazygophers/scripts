package com.lazygophers.lazygit.settings

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertSame

/**
 * 设置的状态对象。`getState` / `loadState` 是 IDE 存盘和读盘的两端，
 * 字段默认值一变就会悄悄改掉所有老用户的行为，所以钉住默认值本身。
 *
 * 这里不碰 PasswordSafe 和 ApplicationManager：那两个要整个 IDE 起来，
 * 而这一层的逻辑（状态往返、默认值）跟它们无关。
 */
class LazyGitSettingsTest {

    @Test
    fun `defaults are the documented ones`() {
        val state = LazyGitSettings.State()
        assertEquals("openai", state.protocol)
        assertEquals("", state.endpoint)
        assertEquals("", state.model)
        assertEquals(0.3, state.temperature)
        assertEquals(15, state.connectTimeoutSeconds)
        assertEquals("zh", state.language)
    }

    @Test
    fun `a fresh component starts from the defaults`() {
        val settings = LazyGitSettings()
        assertEquals(LazyGitSettings.State(), settings.state)
    }

    @Test
    fun `loadState replaces the whole state and api exposes it`() {
        val settings = LazyGitSettings()
        val loaded = LazyGitSettings.State(
            protocol = "anthropic",
            endpoint = "https://api.example.test",
            model = "claude-x",
            temperature = 0.7,
            connectTimeoutSeconds = 30,
            language = "en",
        )
        settings.loadState(loaded)

        assertSame(loaded, settings.state)
        assertEquals("anthropic", settings.api.protocol)
        assertEquals("https://api.example.test", settings.api.endpoint)
        assertEquals(30, settings.api.connectTimeoutSeconds)
    }

    @Test
    fun `the state survives a round trip through getState and loadState`() {
        val first = LazyGitSettings()
        first.loadState(LazyGitSettings.State(protocol = "anthropic", model = "m", endpoint = "e"))

        val second = LazyGitSettings()
        second.loadState(first.state)

        assertEquals(first.state, second.state)
    }

    @Test
    fun `the credential attributes name the service and the key`() {
        val attributes = LazyGitSettings.apiKeyAttributes()
        assertEquals("api-key", attributes.userName)
        // generateServiceName 会拼上 IDE 的前缀，只断言自己那两截在里面
        assertEquals(true, attributes.serviceName.contains("lazy-git"))
        assertEquals(true, attributes.serviceName.contains("api-key"))
    }

    @Test
    fun `ai is not ready while the endpoint or the model is still blank`() {
        val settings = LazyGitSettings()
        assertFalse(settings.api.endpoint.isNotBlank() && settings.api.model.isNotBlank())

        settings.loadState(LazyGitSettings.State(endpoint = "https://api.example.test"))
        assertFalse(settings.api.model.isNotBlank())
    }
}
