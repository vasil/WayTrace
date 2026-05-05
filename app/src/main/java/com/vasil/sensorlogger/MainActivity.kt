package com.vasil.sensorlogger

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.res.ColorStateList
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
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
    private lateinit var tvStatus: TextView

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            service = (binder as RecorderService.LocalBinder).getService()
            service!!.onStateChanged = { runOnUiThread { updateUI() } }
            bound = true
            updateUI()
        }
        override fun onServiceDisconnected(name: ComponentName) {
            bound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        btnToggle = findViewById(R.id.btnToggle)
        btnStop   = findViewById(R.id.btnStop)
        tvStatus  = findViewById(R.id.tvStatus)

        btnToggle.setOnClickListener {
            val svc = service ?: return@setOnClickListener
            when (svc.state) {
                RecordingState.READY, RecordingState.STOPPED -> {
                    startService(Intent(this, RecorderService::class.java))
                    svc.startRecording()
                }
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

        bindService(Intent(this, RecorderService::class.java), connection, Context.BIND_AUTO_CREATE)
        updateUI()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (bound) {
            service?.onStateChanged = null
            unbindService(connection)
            bound = false
        }
    }

    private fun updateUI() {
        val svc   = service
        val state = svc?.state ?: RecordingState.READY

        when (state) {
            RecordingState.READY -> {
                btnToggle.text = "START"
                btnToggle.backgroundTintList = ColorStateList.valueOf(0xFF888888.toInt())
                btnStop.visibility = View.GONE
                tvStatus.text = "Ready"
            }
            RecordingState.RECORDING -> {
                btnToggle.text = "PAUSE"
                btnToggle.backgroundTintList = ColorStateList.valueOf(0xFF007700.toInt())
                btnStop.text = "STOP"
                btnStop.backgroundTintList = ColorStateList.valueOf(0xFFCC0000.toInt())
                btnStop.visibility = View.VISIBLE
                val s   = (svc?.elapsedMs ?: 0L) / 1000
                val dur = "%02d:%02d".format((s % 3600) / 60, s % 60)
                tvStatus.text = "${svc?.currentFile?.name ?: ""}  $dur"
            }
            RecordingState.PAUSED -> {
                btnToggle.text = "RESUME"
                btnToggle.backgroundTintList = ColorStateList.valueOf(0xFFFF8800.toInt())
                btnStop.text = "STOP"
                btnStop.backgroundTintList = ColorStateList.valueOf(0xFFCC0000.toInt())
                btnStop.visibility = View.VISIBLE
                val s   = (svc?.elapsedMs ?: 0L) / 1000
                val dur = "%02d:%02d".format((s % 3600) / 60, s % 60)
                tvStatus.text = "Paused  $dur"
            }
            RecordingState.STOPPED -> {
                btnToggle.text = "START"
                btnToggle.backgroundTintList = ColorStateList.valueOf(0xFF888888.toInt())
                btnStop.visibility = View.GONE
                val sizeKb = (svc?.currentFile?.length() ?: 0L) / 1024
                val s      = (svc?.elapsedMs ?: 0L) / 1000
                val dur    = "%02d:%02d".format((s % 3600) / 60, s % 60)
                tvStatus.text = "${svc?.currentFile?.name ?: ""}  ${sizeKb}KB  $dur"
            }
        }
    }
}
