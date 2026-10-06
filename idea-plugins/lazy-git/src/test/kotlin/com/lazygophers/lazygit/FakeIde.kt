package com.lazygophers.lazygit

import com.intellij.credentialStore.CredentialAttributes
import com.intellij.credentialStore.Credentials
import com.intellij.ide.passwordSafe.PasswordSafe
import com.intellij.ide.passwordSafe.impl.PasswordSafeImpl
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.Application
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.progress.ProcessCanceledException
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.lazygophers.lazygit.settings.LazyGitSettings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import java.lang.reflect.InvocationHandler
import java.lang.reflect.Method
import java.lang.reflect.Proxy

/**
 * 无 IDE fixture 的假 application：只撑起被测代码用到的服务查询
 * （LazyGitSettings / PasswordSafe / ProgressManager，外加测试自己注册的通知与设置页代理）。
 * 不引 test-framework：现有测试风格是不起整个 IDE，这里沿用它。
 */

/** 内存版 CredentialStore：PasswordSafe 读写都落在这张表里。 */
class MemoryCredentialStore : com.intellij.credentialStore.CredentialStore {
    val map = mutableMapOf<CredentialAttributes, Credentials?>()
    override fun get(attributes: CredentialAttributes): Credentials? = map[attributes]
    override fun set(attributes: CredentialAttributes, credentials: Credentials?) {
        if (credentials == null) map.remove(attributes) else map[attributes] = credentials
    }
}

/** 手写的假 ProgressManager：run(Task) 同步执行 task.run，不弹进度框。 */
class FakeProgressManager : ProgressManager() {
    var indicator: ProgressIndicator = FakeIndicator()
    val ranTasks = mutableListOf<Task>()

    /** 用例里预置 indicator（如取消态）；null 恢复默认。run(Task) 时优先用它。 */
    var presetIndicator: ProgressIndicator? = null
    fun overrideIndicator(i: ProgressIndicator?) {
        presetIndicator = i
        indicator = i ?: FakeIndicator()
    }

    override fun getProgressIndicator(): ProgressIndicator = indicator
    override fun hasProgressIndicator(): Boolean = true
    override fun hasModalProgressIndicator(): Boolean = false
    override fun hasUnsafeProgressIndicator(): Boolean = false
    override fun runProcess(runnable: Runnable, processIndicator: ProgressIndicator?) {
        val old = swapIndicator(processIndicator)
        try { runnable.run() } finally { indicator = old }
    }
    override fun <T, E : Throwable> computePrioritized(
        computable: com.intellij.openapi.util.ThrowableComputable<T, E>,
    ): T = computable.compute()
    override fun <X : Any> silenceGlobalIndicator(supplier: java.util.function.Supplier<out X>): X = supplier.get()
    override fun getCurrentProgressModality(): com.intellij.openapi.application.ModalityState? =
        com.intellij.openapi.application.ModalityState.defaultModalityState()
    override fun doCheckCanceled() { indicator.checkCanceled() }
    override fun executeNonCancelableSection(runnable: Runnable) { runnable.run() }
    override fun <T, E : Exception> computeInNonCancelableSection(
        computable: com.intellij.openapi.util.ThrowableComputable<T, E>,
    ): T = computable.compute()
    override fun isInNonCancelableSection(): Boolean = false
    override fun run(task: Task) {
        ranTasks.add(task)
        val old = indicator
        indicator = presetIndicator ?: FakeIndicator()
        try { task.run(indicator) } finally { indicator = old }
    }
    private fun swapIndicator(progress: ProgressIndicator?): ProgressIndicator {
        val old = indicator
        if (progress != null) indicator = progress
        return old
    }
    override fun runProcessWithProgressSynchronously(
        runnable: Runnable, progressTitle: String, cancellable: Boolean, project: Project?,
    ): Boolean { runnable.run(); return true }
    override fun <T, E : Exception> runProcessWithProgressSynchronously(
        computable: com.intellij.openapi.util.ThrowableComputable<T, E>, progressTitle: String,
        cancellable: Boolean, project: Project?,
    ): T = computable.compute()
    override fun runProcessWithProgressSynchronously(
        runnable: Runnable, progressTitle: String, cancellable: Boolean,
        project: Project?, parentComponent: javax.swing.JComponent?,
    ): Boolean { runnable.run(); return true }
    override fun runProcessWithProgressAsynchronously(
        project: Project, progressTitle: String, runnable: Runnable,
        onCancel: Runnable?, onFinished: Runnable?,
        option: com.intellij.openapi.progress.PerformInBackgroundOption,
    ) { runnable.run(); onFinished?.run() }
    override fun runProcessWithProgressAsynchronously(
        backgroundable: Task.Backgroundable, progressIndicator: ProgressIndicator,
    ) { run(backgroundable) }
    override fun executeProcessUnderProgress(runnable: Runnable, progress: ProgressIndicator?) {
        val old = swapIndicator(progress)
        try { runnable.run() } finally { indicator = old }
    }
    override fun runInReadActionWithWriteActionPriority(
        runnable: Runnable, progress: ProgressIndicator?,
    ): Boolean { runnable.run(); return true }
}

/** 最小 ProgressIndicator：isCanceled/text 可控，其余空实现。 */
class FakeIndicator(var canceled: Boolean = false) : ProgressIndicator {
    private var text: String = ""
    override fun start() {}
    override fun stop() {}
    override fun isRunning(): Boolean = true
    override fun cancel() { canceled = true }
    override fun isCanceled(): Boolean = canceled
    override fun setText(value: String?) { text = value ?: "" }
    override fun getText(): String = text
    override fun setText2(value: String?) {}
    override fun getText2(): String = ""
    override fun isIndeterminate(): Boolean = true
    override fun setIndeterminate(indeterminate: Boolean) {}
    override fun setFraction(fraction: Double) {}
    override fun getFraction(): Double = 0.0
    override fun pushState() {}
    override fun popState() {}
    override fun checkCanceled() { if (canceled) throw ProcessCanceledException() }
    override fun isModal(): Boolean = false
    override fun getModalityState(): com.intellij.openapi.application.ModalityState =
        com.intellij.openapi.application.ModalityState.defaultModalityState()
    override fun setModalityProgress(modalityProgress: ProgressIndicator?) {}
    override fun isPopupWasShown(): Boolean = false
    override fun isShowing(): Boolean = false
}

/** 任意接口的空代理：没写 handler 的方法返回合理默认值。 */
fun interfaceProxy(vararg interfaces: Class<*>, handler: (Method, Array<out Any?>) -> Any? = { _, _ -> null }): Any =
    Proxy.newProxyInstance(
        FakeIde::class.java.classLoader, interfaces, InvocationHandler { _, method, args ->
            try { handler(method, args ?: arrayOf()) } catch (_: Exception) { null }
        }
    )

private fun defaultValue(method: Method): Any? = when (method.returnType) {
    Void.TYPE -> null
    Boolean::class.javaPrimitiveType -> false
    Int::class.javaPrimitiveType -> 0
    Long::class.javaPrimitiveType -> 0L
    else -> null
}

/** 组装并安装假 application。每个 JVM 装一次；额外服务用 extraServices 注册。 */
object FakeIde {
    val settings = LazyGitSettings()
    val progressManager = FakeProgressManager()
    val notifications = mutableListOf<Pair<String?, Any?>>() // (text, NotificationType)

    /** 内存 PasswordSafe：LazyGitSettings 的 key 读写走这里。 */
    val passwordSafe: PasswordSafe by lazy {
        val impl = PasswordSafeImpl(CoroutineScope(Dispatchers.Default))
        // setCurrentProvider 是 internal，跨模块不可见，反射设进去
        val m = impl.javaClass.getMethod("setCurrentProvider",
            com.intellij.credentialStore.CredentialStore::class.java)
        m.isAccessible = true
        m.invoke(impl, MemoryCredentialStore())
        impl
    }

    /** PasswordSafe 底下的真实存储，用例里直接读它断言 saveApiKey 效果。 */
    val store: MutableMap<CredentialAttributes, Credentials?>
        get() {
            val m = passwordSafe.javaClass.getMethod("getCurrentProvider")
            m.isAccessible = true
            return (m.invoke(passwordSafe) as MemoryCredentialStore).map
        }

    private val extraServices = mutableMapOf<Class<*>, Any>()
    lateinit var app: Application
        private set

    fun registerService(clazz: Class<*>, instance: Any) {
        extraServices[clazz] = instance
    }

    private fun buildApp(): Application {
        val handler = InvocationHandler { _, method, args ->
            when (method.name) {
                "getService" -> when (val clazz = args!![0] as Class<*>) {
                    LazyGitSettings::class.java -> settings
                    PasswordSafe::class.java -> passwordSafe
                    ProgressManager::class.java -> progressManager
                    else -> extraServices[clazz]
                        ?: interfaceProxy(clazz) { m, _ -> defaultValue(m) }
                }
                "runReadAction", "runWriteAction" -> {
                    val arg = args!![0]
                    (arg as? java.util.concurrent.Callable<Any?>)?.call()
                        ?: (arg as? Runnable)?.run()
                }
                "executeOnPooledThread" -> { (args!![0] as Runnable).run(); null }
                "invokeLater" -> { (args!![0] as Runnable).run(); Unit }
                "isUnitTestMode", "isDispatchThread", "isWriteThread", "isReadAccessAllowed",
                "isWriteAccessAllowed", "isHeadlessEnvironment", "isCommandLine" -> true
                else -> defaultValue(method)
            }
        }
        return Proxy.newProxyInstance(
            FakeIde::class.java.classLoader, arrayOf(Application::class.java), handler
        ) as Application
    }

    fun install() {
        if (FakeIde::app.isInitialized) return
        app = buildApp()
        ApplicationManager.setApplication(app, Disposable { })
        // AnActionEvent.createEvent 内部会 ActionManager.getInstance()；
        // 真实 ActionManagerImpl 构造需要完整 IDE 环境，用 mockito 造空壳
        extraServices[com.intellij.openapi.actionSystem.ActionManager::class.java] =
            org.mockito.Mockito.mock(com.intellij.openapi.actionSystem.ActionManager::class.java)
    }
}

/** 供 action 测试用：手工 new 出的 Project 代理。 */
fun fakeProject(): Project {
    val bus = interfaceProxy(
        com.intellij.util.messages.MessageBus::class.java
    ) { method, args ->
        when (method.name) {
            "syncPublisher" -> interfaceProxy(
                com.intellij.notification.Notifications::class.java
            ) { m, a ->
                when (m.name) {
                    "notify" -> {
                        val n = a?.getOrNull(0) as? com.intellij.notification.Notification
                        FakeIde.notifications.add((n?.content ?: "") to (n?.type))
                    }
                    else -> defaultValue(m)
                }
            }
            else -> defaultValue(method)
        }
    }
    return Proxy.newProxyInstance(
        FakeIde::class.java.classLoader, arrayOf(Project::class.java)
    ) { _, method, _ ->
        when (method.name) {
            "getName" -> "fake-project"
            "getMessageBus" -> bus
            else -> defaultValue(method)
        }
    } as Project
}
