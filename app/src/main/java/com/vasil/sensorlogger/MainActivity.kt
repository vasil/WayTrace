package com.vasil.sensorlogger

import android.app.AlertDialog
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
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
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private var service: RecorderService? = null
    private var bound = false

    private var stopConfirmPending = false
    private val stopConfirmHandler = Handler(Looper.getMainLooper())

    private lateinit var btnToggle: Button
    private lateinit var btnStop: Button
    private lateinit var btnPin: Button
    private lateinit var tvStatus: TextView

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            service = (binder as RecorderService.LocalBinder).getService()
            service!!.onStateChanged = { runOnUiThread { updateUI() } }
            bound = true
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
                tvStatus.text = "Ready"
            }
            RecordingState.RECORDING -> {
                btnToggle.text = "PAUSE"
                btnToggle.backgroundTintList = color(0xFF007700)
                btnToggle.setTextColor(0xFFFF8800.toInt())
                btnStop.backgroundTintList = color(0xFFCC0000)
                btnStop.visibility = View.VISIBLE
                btnPin.visibility  = View.VISIBLE
                val pinCount = svc?.pinpointCount ?: 0
                btnPin.text = if (pinCount == 0) "PIN" else "PIN $pinCount"
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                val sizeMb = "%.1f".format((svc?.getFileSize() ?: 0L) / (1024.0 * 1024.0))
                tvStatus.text = "${svc?.currentFileName ?: ""}  $dur  ${sizeMb}MB"
            }
            RecordingState.PAUSED -> {
                btnToggle.text = "RESUME"
                btnToggle.backgroundTintList = color(0xFFFF8800)
                btnToggle.setTextColor(0xFF00CC00.toInt())
                btnStop.backgroundTintList = color(0xFFCC0000)
                btnStop.visibility = View.VISIBLE
                btnPin.visibility  = View.VISIBLE
                val pinCount = svc?.pinpointCount ?: 0
                btnPin.text = if (pinCount == 0) "PIN" else "PIN $pinCount"
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                val sizeMb = "%.1f".format((svc?.getFileSize() ?: 0L) / (1024.0 * 1024.0))
                tvStatus.text = "${svc?.currentFileName ?: ""}  $dur  ${sizeMb}MB"
            }
            RecordingState.STOPPED -> {
                btnToggle.text = "START"
                btnToggle.backgroundTintList = color(0xFF888888)
                btnToggle.setTextColor(0xFF00CC00.toInt())
                btnStop.visibility = View.GONE
                btnPin.visibility  = View.GONE
                val sizeKb = (svc?.getFileSize() ?: 0L) / 1024
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                tvStatus.text = "${svc?.currentFileName ?: ""}  ${sizeKb}KB  $dur"
            }
        }
    }
}
