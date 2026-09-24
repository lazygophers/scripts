package com.lazygophers.lazygit.settings

import com.intellij.credentialStore.CredentialAttributes
import com.intellij.credentialStore.generateServiceName
import com.intellij.ide.passwordSafe.PasswordSafe
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage

/** 非敏感配置。API key 走 PasswordSafe，不进 state。 */
@Service
@State(name = "LazyGitSettings", storages = [Storage("lazy-git.xml")])
class LazyGitSettings : PersistentStateComponent<LazyGitSettings.State> {

    data class State(
        var protocol: String = "openai",         // openai | anthropic
        var endpoint: String = "",               // base URL，如 https://api.example.com
        var model: String = "",
        var temperature: Double = 0.3,
        var connectTimeoutSeconds: Int = 15,
        var language: String = "zh",             // zh | en：commit message 语言，用户选定
    )

    private var state = State()

    override fun getState(): State = state
    override fun loadState(s: State) {
        state = s
    }

    val api: State get() = state

    fun apiKey(): String? = PasswordSafe.instance.getPassword(apiKeyAttributes())

    fun saveApiKey(key: String?) {
        if (key.isNullOrBlank()) {
            PasswordSafe.instance.setPassword(apiKeyAttributes(), null)
        } else {
            PasswordSafe.instance.setPassword(apiKeyAttributes(), key)
        }
    }

    fun isAiReady(): Boolean =
        state.endpoint.isNotBlank() && state.model.isNotBlank() && !apiKey().isNullOrBlank()

    companion object {
        private const val SERVICE = "lazy-git"

        fun apiKeyAttributes(): CredentialAttributes =
            CredentialAttributes(generateServiceName(SERVICE, "api-key"), "api-key")

        @JvmStatic
        fun getInstance(): LazyGitSettings =
            ApplicationManager.getApplication().getService(LazyGitSettings::class.java)
    }
}
