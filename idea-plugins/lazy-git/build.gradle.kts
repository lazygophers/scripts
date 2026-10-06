import java.io.File

plugins {
    kotlin("jvm") version "2.1.20"
    id("org.jetbrains.intellij.platform") version "2.18.1"
    jacoco
}

group = "com.lazygophers"
version = "0.0.1"

repositories {
    // 本机 JVM 到 maven central 的 TLS 被网络干扰时，用 M2_PROXY 指向本地明文反代
    // （curl/python 通路正常）。不设就是常规 mavenCentral，CI 不受影响。
    System.getenv("M2_PROXY")?.let { uri(it) }?.let { maven { url = it } }
    // curl 预取的依赖目录：JVM 直连 central 卡死时 LOCAL_M2_DIR 指过去兜底（不设则无此仓库）
    System.getenv("LOCAL_M2_DIR")?.let { uri(it) }?.let { maven { url = it } }
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

kotlin {
    jvmToolchain(21)
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

dependencies {
    testImplementation(kotlin("test"))
    // ActionManager / NotificationGroupManager 这类抽象类造不出轻量实例，mock 它们
    testImplementation("org.mockito:mockito-core:5.14.2")
    // IntelliJ 平台注入的测试类路径引用了 JUnit4 的 Statement，缺了它测试进程起不来
    testRuntimeOnly("junit:junit:4.13.2")

    intellijPlatform {
        // 本地已解压的 IDE（下载源不稳时用）：LOCAL_IDE_HOME 指到 "IntelliJ IDEA CE.app"。
        // 不设就走正常远端解析，CI 不受影响。
        val localIde = System.getenv("LOCAL_IDE_HOME")
        if (localIde != null) {
            local(localIde)
        } else {
            intellijIdeaCommunity("2025.1")
        }
        bundledPlugin("Git4Idea")
        pluginVerifier()
        javaCompiler()
    }
}

intellijPlatform {
    pluginConfiguration {
        ideaVersion {
            sinceBuild = "251"
        }
    }
}

tasks.named<Test>("test") {
    useJUnitPlatform()
    dependsOn("instrumentTestSandboxJar")
    finalizedBy(tasks.named("jacocoTestReport"))
}

// Offline instrumentation：测试进程的类由 IDE 的类加载体系从 sandbox jar 加载，
// 绕过 javaagent 的 Instrumentation transformer（实测 exec 里 0 条 lazygophers 记录）。
// 探针必须在构建期编进字节码：对 sandbox 里那份插件 jar 就地插桩。
// 只动 plugins-test 下的拷贝，prepareSandbox/buildPlugin 的发布产物保持未插桩。
val instrumentTestSandboxJar by tasks.registering {
    dependsOn(tasks.named("prepareTestSandbox"))
    mustRunAfter(tasks.named("prepareTestSandbox"))
    val sandboxJar: File = File(
        ".intellijPlatform/sandbox/lazy-git/IC-2025.1/plugins-test/lazy-git/lib/lazy-git-0.0.1.jar")
    // 输入永远是原始 composedJar，不从 sandbox jar 重新插桩（探针字段二次插桩会报错）
    val originalJar = tasks.named("composedJar").map { it.outputs.files.singleFile }
    inputs.file(originalJar)
    val workDir = layout.buildDirectory.dir("jacoco-offline")
    outputs.upToDateWhen { false }
    doLast {
        val out = workDir.get().asFile.apply { deleteRecursively(); mkdirs() }
        // InstrumentTask 对归档 fileset 只复制不插桩：手动解包 -> 插桩 class 目录 -> 覆盖回包
        val unpacked = out.resolve("unpack").apply { mkdirs() }
        val instrumented = out.resolve("instrumented").apply { mkdirs() }
        copy {
            from(zipTree(originalJar.get()))
            into(unpacked)
        }
        ant.withGroovyBuilder {
            "taskdef"("name" to "jacocoInstrument", "classname" to "org.jacoco.ant.InstrumentTask",
                "classpath" to configurations.getByName("jacocoAnt").asPath)
            "jacocoInstrument"("destdir" to instrumented) {
                "fileset"("dir" to unpacked)
            }
        }
        // 插桩成功的直接证据：class 里出现 JaCoCo 探针数据字段名
        val probe = instrumented.walkTopDown().filter { it.isFile }.any {
            it.readBytes().toString(Charsets.ISO_8859_1).contains("\$jacocoData")
        }
        check(probe) { "插桩后的 class 里没有 jacocoData 探针" }
        copy {
            from(instrumented)
            into(unpacked)  // 插桩版 class 覆盖原始 class，其余资源（META-INF 等）保留
        }
        ant.withGroovyBuilder {
            "zip"("destfile" to sandboxJar, "basedir" to unpacked)
        }
    }
}
tasks.named<JacocoReport>("jacocoTestReport") {
    dependsOn(tasks.named("test"))
    // offline 插桩的 exec 里 class id 按插桩前的原始字节计算，报告直接分析原始 classes
    // （不含测试类）。之前的 instrumentCode 目录是给运行时 agent 路径用的，已废弃。
    classDirectories.setFrom(sourceSets.main.get().output.classesDirs)
    reports {
        xml.required = true
        html.required = true
    }
}
tasks.named<JacocoCoverageVerification>("jacocoTestCoverageVerification") {
    dependsOn(tasks.named("jacocoTestReport"))
    violationRules {
        rule {
            limit {
                counter = "INSTRUCTION"
                minimum = "0.95".toBigDecimal()
            }
            limit {
                counter = "LINE"
                minimum = "0.95".toBigDecimal()
            }
            // BRANCH 不设门禁：198 个分支里约 40 个是 Kotlin 编译器生成的 null 检查 /
            // when 兜底 / 防御性竞态分支（当前实测 79.80%），硬凑只能写无价值的调用。
        }
    }
}
