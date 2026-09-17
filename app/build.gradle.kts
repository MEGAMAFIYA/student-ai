plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "uz.studentai.mobile"
    compileSdk = 34

    defaultConfig {
        applicationId = "uz.studentai.mobile"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0-phase1"

        // 🔧 Botning HTTP serveri (bot.py) qaysi manzilda ochiq bo'lsa,
        // ilova SHU manzilga ulanadi. Render'ga deploy qilingandan keyin
        // BASE_URL'ni haqiqiy https manzilga almashtiring (masalan
        // "https://student-ai-uz.onrender.com/"). Oxirida "/" bo'lishi shart.
        buildConfigField("String", "BASE_URL", "\"https://student-ai-uz.onrender.com/\"")
    }

    buildTypes {
        release {
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
