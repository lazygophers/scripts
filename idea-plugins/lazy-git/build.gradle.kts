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
    finalizedBy(tasks.named("jacocoTestReport"))
}
tasks.named<JacocoReport>("jacocoTestReport") {
    dependsOn(tasks.named("test"))
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
                minimum = "0.95".toBigDecimal()
            }
        }
    }
}
