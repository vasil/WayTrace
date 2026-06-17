package com.vasil.sensorlogger

import android.app.AlertDialog
import android.content.ComponentName
import android.os.Build
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.SharedPreferences
import android.content.res.ColorStateList
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.util.DisplayMetrics
import android.view.MotionEvent
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private var service: RecorderService? = null
    private var bound = false

    private var stopConfirmPending = false
    private val stopConfirmHandler = Handler(Looper.getMainLooper())

    private lateinit var btnToggle: Button
    private lateinit var btnStop: Button
    private lateinit var btnPin: Button
    private lateinit var btnSync: Button
    private lateinit var tvStatus: TextView

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            service = (binder as RecorderService.LocalBinder).getService()
            service!!.onStateChanged = { runOnUiThread { updateUI() } }
            bound = true
            // Re-apply live mode setting if it was on before app restart
            if (livePrefs.getBoolean(PREF_LIVE_UNLOCKED, false)
                && livePrefs.getBoolean(PREF_LIVE_ENABLED, false)) {
                val ip   = livePrefs.getString(PREF_LIVE_IP,   DEFAULT_LIVE_IP) ?: DEFAULT_LIVE_IP
                val port = livePrefs.getInt(PREF_LIVE_PORT,    DEFAULT_LIVE_PORT)
                service!!.enableLiveMode(ip, port)
            }
            updateUI()
        }
        override fun onServiceDisconnected(name: ComponentName) { bound = false }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        btnToggle = findViewById(R.id.btnToggle)
        btnStop   = findViewById(R.id.btnStop)
        btnPin    = findViewById(R.id.btnPin)
        btnSync   = findViewById(R.id.btnSync)
        tvStatus  = findViewById(R.id.tvStatus)

        btnToggle.setOnClickListener {
            val svc = service ?: return@setOnClickListener
            when (svc.state) {
                RecordingState.READY, RecordingState.STOPPED -> svc.startRecording()
                RecordingState.RECORDING -> svc.pauseRecording()
                RecordingState.PAUSED    -> svc.resumeRecording()
            }
        }

        btnStop.setOnClickListener {
            if (!stopConfirmPending) {
                stopConfirmPending = true
                btnStop.text = "SURE?"
                stopConfirmHandler.postDelayed({
                    stopConfirmPending = false
                    btnStop.text = "STOP"
                }, 3000)
            } else {
                stopConfirmHandler.removeCallbacksAndMessages(null)
                stopConfirmPending = false
                service?.stopRecording()
            }
        }

        btnPin.setOnClickListener { service?.pinpoint() }

        btnSync.setOnClickListener { service?.syncPulse() }

        installLiveModeEasterEgg()

        requestBatteryOptimizationExemption()
        startService(Intent(this, RecorderService::class.java))
        bindService(Intent(this, RecorderService::class.java), connection, Context.BIND_AUTO_CREATE)
        handleIntent(intent)
        updateUI()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(RecorderService.EXTRA_STOP_DIALOG, false) == true) {
            showStopDialog()
        }
    }

    // ── OSI-019: Live Sonification easter egg ─────────────────────────────
    //
    // Hidden by default. Five long-presses on the status line within 10
    // seconds reveal a 📡 icon prepended to the status text. Tapping the
    // status while the icon is showing opens the IP/port/toggle dialog.
    // The unlock state is persisted in SharedPreferences so it survives
    // app restarts; long-pressing 5× again hides it.

    private val livePrefs: SharedPreferences
        get() = getSharedPreferences("waytrace_live", Context.MODE_PRIVATE)

    private val PREF_LIVE_UNLOCKED = "live_unlocked"
    private val PREF_LIVE_ENABLED  = "live_enabled"
    private val PREF_LIVE_IP       = "live_ip"
    private val PREF_LIVE_PORT     = "live_port"
    private val DEFAULT_LIVE_IP    = "10.0.0.34"
    private val DEFAULT_LIVE_PORT  = 54321

    // Two-corner-tap gesture state. Accessibility-friendly: doesn't depend on
    // tap counts (so no conflict with triple-tap-to-zoom). User taps the
    // top-left corner, then the top-right corner, within 3 seconds.
    private var firstCornerAt = 0L
    private val CORNER_WINDOW_MS = 3_000L

    private fun installLiveModeEasterEgg() {
        // Status line is now just the open-config tap (when unlocked).
        tvStatus.setOnClickListener {
            if (livePrefs.getBoolean(PREF_LIVE_UNLOCKED, false)) {
                showLiveModeDialog()
            }
        }
    }

    /** Detect the corner-tap sequence at the activity level so it works
     *  regardless of which view is touched. Returns false from
     *  dispatchTouchEvent so the touch still reaches its target view. */
    private fun handleCornerTap(ev: MotionEvent) {
        if (ev.action != MotionEvent.ACTION_DOWN) return
        val dm = DisplayMetrics()
        windowManager.defaultDisplay.getMetrics(dm)
        val w = dm.widthPixels
        val h = dm.heightPixels
        val cornerW = w / 4         // left/right corner is 1/4 of screen wide
        val cornerH = h / 8         // top corner zone is 1/8 of screen tall
        val x = ev.x.toInt()
        val y = ev.y.toInt()
        val inTopLeft  = (x < cornerW)         && (y < cornerH)
        val inTopRight = (x > (w - cornerW))   && (y < cornerH)
        val now = System.currentTimeMillis()
        if (firstCornerAt == 0L || now - firstCornerAt > CORNER_WINDOW_MS) {
            if (inTopLeft) {
                firstCornerAt = now
                Toast.makeText(this, "↗ now tap top-right corner",
                               Toast.LENGTH_SHORT).show()
            }
            return
        }
        // We have a recent top-left tap; check for top-right second tap
        if (inTopRight) {
            firstCornerAt = 0L
            val newUnlocked = !livePrefs.getBoolean(PREF_LIVE_UNLOCKED, false)
            livePrefs.edit().putBoolean(PREF_LIVE_UNLOCKED, newUnlocked).apply()
            if (!newUnlocked) {
                livePrefs.edit().putBoolean(PREF_LIVE_ENABLED, false).apply()
                service?.disableLiveMode()
                Toast.makeText(this, "live mode hidden", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "live mode unlocked — tap status to configure",
                               Toast.LENGTH_LONG).show()
            }
            updateUI()
        } else if (!inTopLeft) {
            // Tapped elsewhere within the window — reset, silently
            firstCornerAt = 0L
        }
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        handleCornerTap(ev)
        return super.dispatchTouchEvent(ev)
    }

    private fun showLiveModeDialog() {
        val ip   = livePrefs.getString(PREF_LIVE_IP,   DEFAULT_LIVE_IP) ?: DEFAULT_LIVE_IP
        val port = livePrefs.getInt(PREF_LIVE_PORT,    DEFAULT_LIVE_PORT)
        val on   = livePrefs.getBoolean(PREF_LIVE_ENABLED, false)

        val ipField   = EditText(this).apply { setText(ip);   hint = "laptop IP" }
        val portField = EditText(this).apply { setText(port.toString()); hint = "port" }
        val toggle    = CheckBox(this).apply { text = "live streaming ON"; isChecked = on }
        val layout    = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 24)
            addView(ipField); addView(portField); addView(toggle)
        }
        AlertDialog.Builder(this)
            .setTitle("📡  Live Sonify")
            .setView(layout)
            .setPositiveButton("Save") { _, _ ->
                val newIp   = ipField.text.toString().trim().ifEmpty { DEFAULT_LIVE_IP }
                val newPort = portField.text.toString().toIntOrNull() ?: DEFAULT_LIVE_PORT
                val newOn   = toggle.isChecked
                livePrefs.edit()
                    .putString(PREF_LIVE_IP, newIp)
                    .putInt(PREF_LIVE_PORT, newPort)
                    .putBoolean(PREF_LIVE_ENABLED, newOn)
                    .apply()
                val svc = service
                if (svc != null) {
                    if (newOn) svc.enableLiveMode(newIp, newPort) else svc.disableLiveMode()
                }
                updateUI()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun statusPrefix(): String {
        if (!livePrefs.getBoolean(PREF_LIVE_UNLOCKED, false)) return ""
        return if (livePrefs.getBoolean(PREF_LIVE_ENABLED, false)) "📡 " else "📡· "
    }

    private fun requestBatteryOptimizationExemption() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                data = Uri.parse("package:$packageName")
            })
        }
    }

    private fun showStopDialog() {
        AlertDialog.Builder(this)
            .setTitle("Stop recording?")
            .setMessage("The CSV file will be saved to Downloads.")
            .setPositiveButton("Stop") { _, _ -> service?.stopRecording() }
            .setNegativeButton("Cancel", null)
            .show()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (bound) {
            service?.onStateChanged = null
            unbindService(connection)
            bound = false
        }
    }

    private fun color(c: Long) = ColorStateList.valueOf(c.toInt())

    private fun updateUI() {
        val svc   = service
        val state = svc?.state ?: RecordingState.READY

        // Reset stop confirm if state changed
        if (state == RecordingState.STOPPED || state == RecordingState.READY) {
            stopConfirmPending = false
            btnStop.text = "STOP"
        }

        when (state) {
            RecordingState.READY -> {
                btnToggle.text = "START"
                btnToggle.backgroundTintList = color(0xFF888888)
                btnToggle.setTextColor(0xFF00CC00.toInt())
                btnStop.visibility = View.GONE
                btnPin.visibility  = View.GONE
                btnSync.visibility = View.GONE
                tvStatus.text = "${statusPrefix()}Ready"
            }
            RecordingState.RECORDING -> {
                btnToggle.text = "PAUSE"
                btnToggle.backgroundTintList = color(0xFF007700)
                btnToggle.setTextColor(0xFFFF8800.toInt())
                btnStop.backgroundTintList = color(0xFFCC0000)
                btnStop.visibility = View.VISIBLE
                btnPin.visibility  = View.VISIBLE
                btnSync.visibility = View.VISIBLE
                val pinCount = svc?.pinpointCount ?: 0
                btnPin.text = if (pinCount == 0) "PIN" else "PIN $pinCount"
                val syncCount = svc?.syncPulseCount ?: 0
                btnSync.text = if (syncCount == 0) "SYNC" else "SYNC $syncCount"
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                val sizeMb = "%.1f".format((svc?.getFileSize() ?: 0L) / (1024.0 * 1024.0))
                tvStatus.text = "${statusPrefix()}${svc?.currentFileName ?: ""}  $dur  ${sizeMb}MB"
            }
            RecordingState.PAUSED -> {
                btnToggle.text = "RESUME"
                btnToggle.backgroundTintList = color(0xFFFF8800)
                btnToggle.setTextColor(0xFF00CC00.toInt())
                btnStop.backgroundTintList = color(0xFFCC0000)
                btnStop.visibility = View.VISIBLE
                btnPin.visibility  = View.VISIBLE
                btnSync.visibility = View.VISIBLE
                val pinCount = svc?.pinpointCount ?: 0
                btnPin.text = if (pinCount == 0) "PIN" else "PIN $pinCount"
                val syncCount = svc?.syncPulseCount ?: 0
                btnSync.text = if (syncCount == 0) "SYNC" else "SYNC $syncCount"
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                val sizeMb = "%.1f".format((svc?.getFileSize() ?: 0L) / (1024.0 * 1024.0))
                tvStatus.text = "${statusPrefix()}${svc?.currentFileName ?: ""}  $dur  ${sizeMb}MB"
            }
            RecordingState.STOPPED -> {
                btnToggle.text = "START"
                btnToggle.backgroundTintList = color(0xFF888888)
                btnToggle.setTextColor(0xFF00CC00.toInt())
                btnStop.visibility = View.GONE
                btnPin.visibility  = View.GONE
                btnSync.visibility = View.GONE
                val sizeKb = (svc?.getFileSize() ?: 0L) / 1024
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                tvStatus.text = "${statusPrefix()}${svc?.currentFileName ?: ""}  ${sizeKb}KB  $dur"
            }
        }
    }
}
