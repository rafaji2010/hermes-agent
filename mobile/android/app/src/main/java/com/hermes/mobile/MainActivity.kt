package com.hermes.mobile

import android.app.Activity
import android.os.Bundle

/**
 * Hermes Mobile — placeholder shell.
 *
 * No gateway wiring yet. This activity only renders the placeholder "Hermes
 * Mobile" screen so the project proves it compiles and launches. The gateway
 * client contract this app will implement lives in mobile/src/gateway-types.ts
 * (mirrored from the desktop app's real wire shapes).
 */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
    }
}
