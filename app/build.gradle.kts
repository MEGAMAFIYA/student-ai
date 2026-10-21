plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "uz.studentai.mobile"
    compileSdk = 34
    ndkVersion = "27.2.12479018"

    defaultConfig {
        applicationId = "uz.studentai.mobile"
        minSdk = 24
        targetSdk = 34
        // CI'da build raqami va commit qo'shiladi: APK qaysi koddan chiqqanini aniq bilish uchun.
        versionCode = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull()?.plus(100) ?: 2
        // Ko'proq Android telefonlariga mos universal APK.
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }

        // CMake arguments belong under defaultConfig.externalNativeBuild.cmake.
        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DLLAMA_CPP_SOURCE_DIR=${rootProject.projectDir.resolve("llama.cpp").absolutePath}",
                    "-DGGML_OPENMP=OFF",
                    "-DGGML_LLAMAFILE=OFF",
                    "-DGGML_NATIVE=OFF",
                    "-DLLAMA_BUILD_TESTS=OFF",
                    "-DLLAMA_BUILD_EXAMPLES=OFF",
                    "-DLLAMA_BUILD_SERVER=OFF"
                )
            }
        }

        versionName = "1.1." + (System.getenv("GITHUB_RUN_NUMBER") ?: "0") + (System.getenv("GITHUB_SHA")?.take(7)?.let { "-$it" } ?: "")

        // 🔧 Botning HTTP serveri (bot.py) qaysi manzilda ochiq bo'lsa,
        // ilova SHU manzilga ulanadi. Render'ga deploy qilingandan keyin
        // BASE_URL'ni haqiqiy https manzilga almashtiring (masalan
        // "https://student-ai-uz.onrender.com/"). Oxirida "/" bo'lishi shart.
        // CI'da GitHub repo Variables (Settings > Secrets and variables > Actions > Variables)
        // ichida BASE_URL=https://SIZNING-SERVIS.onrender.com qilib berilsa, shu ishlatiladi.
        val baseUrl = (System.getenv("BASE_URL")?.trim()?.takeIf { it.startsWith("http") }
            ?: "https://student-flah.onrender.com").trimEnd('/') + "/"
        buildConfigField("String", "BASE_URL", "\"$baseUrl\"")
    }

    // GitHub Secrets'dagi doimiy kalit bilan imzolash: shunda har yangi APK eskisining USTIGA o'rnatiladi.
    signingConfigs {
        create("release") {
            val ksPath = System.getenv("ANDROID_KEYSTORE_PATH")
            if (!ksPath.isNullOrBlank() && file(ksPath).exists()) {
                storeFile = file(ksPath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release").takeIf { it.storeFile != null }
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        debug {
            // Debug build'da HTTP (masalan mahalliy test server, https bo'lmasa
            // ham) ruxsat berilishi uchun network_security_config ishlatiladi.
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")

    // 🌐 Tarmoq — backend (mobile_api.py) bilan JSON orqali gaplashish uchun
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // 🔁 Coroutines — tarmoq so'rovlarini UI thread'ni bloklamasdan bajarish
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
