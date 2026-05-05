package com.vasil.sensorlogger

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.io.FileWriter
import java.io.PrintWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs
import kotlin.math.sqrt

enum class RecordingState { READY, RECORDING, PAUSED, STOPPED }

class MainActivity : AppCompatActivity(), SensorEventListener {

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    private var state = RecordingState.READY
    private var writer: PrintWriter? = null
    private var currentFile: File? = null

    private val INTERVAL_NS = 100_000_000L
    private var lastAccelTime = 0L
    private var lastGyroTime = 0L

    private val EVENT_COOLDOWN_NS = 500_000_000L
    private var lastBumpTime = 0L
    private var lastFallTime = 0L
    private var lastWheelieTime = 0L
    private var lastTiltTime = 0L

    private var bumpCount = 0
    private var maxMagnitude = 0f
    private var startTimeMs = 0L
    private var elapsedMs = 0L
    private var startDisplayTime = ""

    private var stopConfirmPending = false
    private val stopConfirmHandler = Handler(Looper.getMainLooper())

    private val timerHandler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            elapsedMs = System.currentTimeMillis() - startTimeMs
            updateStats()
            timerHandler.postDelayed(this, 1000)
        }
    }

    private lateinit var btnToggle: Button
    private lateinit var btnStop: Button
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        btnToggle = findViewById(R.id.btnToggle)
        btnStop   = findViewById(R.id.btnStop)
        tvStatus  = findViewById(R.id.tvStatus)

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope     = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)

        btnToggle.setOnClickListener {
            when (state) {
                RecordingState.READY, RecordingState.STOPPED -> startRecording()
                RecordingState.RECORDING -> pauseRecording()
                RecordingState.PAUSED    -> resumeRecording()
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
                stopRecording()
            }
        }

        updateUI()
    }

    // ── Recording state machine ────────────────────────────────────────────────

    private fun startRecording() {
        try {
            val now = Date()
            val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(now)
            startDisplayTime = SimpleDateFormat("HH:mm", Locale.getDefault()).format(now)

            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            dir.mkdirs()

            currentFile = File(dir, "sensors_$timestamp.csv")
            writer = PrintWriter(FileWriter(currentFile!!), true)
            writer!!.println("timestamp_ms,sensor,x,y,z,event")

            bumpCount = 0
            maxMagnitude = 0f
            lastAccelTime = 0L
            lastGyroTime = 0L
            startTimeMs = System.currentTimeMillis()
            elapsedMs = 0L

            registerSensors()
            state = RecordingState.RECORDING
            updateUI()
            timerHandler.post(timerRunnable)

        } catch (e: Exception) {
            tvStatus.text = "Error: ${e.message}"
        }
    }

    private fun pauseRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        elapsedMs = System.currentTimeMillis() - startTimeMs
        state = RecordingState.PAUSED
        updateUI()
    }

    private fun resumeRecording() {
        startTimeMs = System.currentTimeMillis() - elapsedMs
        registerSensors()
        state = RecordingState.RECORDING
        updateUI()
        timerHandler.post(timerRunnable)
    }

    private fun stopRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        if (state == RecordingState.RECORDING) elapsedMs = System.currentTimeMillis() - startTimeMs
        writer?.close()
        writer = null
        stopConfirmPending = false
        state = RecordingState.STOPPED
        updateUI()
    }

    private fun registerSensors() {
        sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_GAME)
        sensorManager.registerListener(this, gyroscope, SensorManager.SENSOR_DELAY_GAME)
    }

    // ── Sensor events ─────────────────────────────────────────────────────────

    override fun onSensorChanged(event: SensorEvent) {
        if (state != RecordingState.RECORDING) return
        val nowNs = event.timestamp

        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                if (lastAccelTime != 0L && nowNs - lastAccelTime < INTERVAL_NS) return
                lastAccelTime = nowNs
                val mag = sqrt(event.values[0] * event.values[0] +
                        event.values[1] * event.values[1] +
                        event.values[2] * event.values[2])
                if (mag > maxMagnitude) maxMagnitude = mag
                val ev = detectAccelEvent(nowNs, event.values, mag)
                if (ev == "bump" || ev == "heavy_bump") bumpCount++
                writeLine(nowNs, "accel", event.values, ev)
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                writeLine(nowNs, "gyro", event.values, detectGyroEvent(nowNs, event.values))
            }
        }
    }

    private fun detectAccelEvent(nowNs: Long, v: FloatArray, mag: Float): String {
        // ISO 2631-5: clinically significant single-event shock (≥1g above 9.8 baseline)
        if (mag > 20.0f && nowNs - lastBumpTime > EVENT_COOLDOWN_NS) {
            lastBumpTime = nowNs; return "heavy_bump"
        }
        // ISO 2631-1: "quite uncomfortable" single event (≥0.5g above 9.8 baseline)
        if (mag > 15.0f && nowNs - lastBumpTime > EVENT_COOLDOWN_NS) {
            lastBumpTime = nowNs; return "bump"
        }
        if (v[1] < -15.0f && nowNs - lastFallTime > EVENT_COOLDOWN_NS) {
            lastFallTime = nowNs; return "fall"
        }
        return ""
    }

    private fun detectGyroEvent(nowNs: Long, v: FloatArray): String {
        if (abs(v[2]) > 3.0f && nowNs - lastWheelieTime > EVENT_COOLDOWN_NS) {
            lastWheelieTime = nowNs; return "wheelie"
        }
        if (abs(v[0]) > 3.0f && nowNs - lastTiltTime > EVENT_COOLDOWN_NS) {
            lastTiltTime = nowNs; return "tilt"
        }
        return ""
    }

    private fun writeLine(tsNs: Long, sensor: String, v: FloatArray, event: String) {
        writer?.println("${tsNs / 1_000_000L},$sensor,${v[0]},${v[1]},${v[2]},$event")
    }

    // ── UI ────────────────────────────────────────────────────────────────────

    private fun updateStats() {
        val s = elapsedMs / 1000
        val dur = "%02d:%02d".format((s % 3600) / 60, s % 60)
        tvStatus.text = "Started: $startDisplayTime | Duration: $dur | Bumps: $bumpCount | Max: ${"%.1f".format(maxMagnitude)} m/s²"
    }

    private fun updateUI() {
        when (state) {
            RecordingState.READY -> {
                btnToggle.text = "START"; btnToggle.setBackgroundColor(0xFF888888.toInt())
                btnStop.visibility = View.GONE; tvStatus.text = "Ready"
            }
            RecordingState.RECORDING -> {
                btnToggle.text = "PAUSE"; btnToggle.setBackgroundColor(0xFF007700.toInt())
                btnStop.text = "STOP"; btnStop.setBackgroundColor(0xFFCC0000.toInt())
                btnStop.visibility = View.VISIBLE; updateStats()
            }
            RecordingState.PAUSED -> {
                btnToggle.text = "RESUME"; btnToggle.setBackgroundColor(0xFFFF8800.toInt())
                btnStop.text = "STOP"; btnStop.setBackgroundColor(0xFFCC0000.toInt())
                btnStop.visibility = View.VISIBLE
            }
            RecordingState.STOPPED -> {
                btnToggle.text = "START"; btnToggle.setBackgroundColor(0xFF888888.toInt())
                btnStop.visibility = View.GONE
                val s = elapsedMs / 1000
                val dur = "%02d:%02d".format((s % 3600) / 60, s % 60)
                val sizeKb = (currentFile?.length() ?: 0L) / 1024
                tvStatus.text = "${currentFile?.name}\n$dur | Bumps: $bumpCount | Max: ${"%.1f".format(maxMagnitude)} m/s² | ${sizeKb}KB"
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) stopRecording()
    }
}
