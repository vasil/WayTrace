package com.vasil.sensorlogger

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Binder
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import java.io.File
import java.io.FileWriter
import java.io.PrintWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs
import kotlin.math.sqrt

class RecorderService : Service(), SensorEventListener {

    inner class LocalBinder : Binder() {
        fun getService(): RecorderService = this@RecorderService
    }

    private val binder = LocalBinder()
    var onStateChanged: (() -> Unit)? = null

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    var state = RecordingState.READY
        private set

    private var writer: PrintWriter? = null
    var currentFile: File? = null
        private set

    private val INTERVAL_NS = 100_000_000L
    private var lastAccelTime = 0L
    private var lastGyroTime = 0L

    private val EVENT_COOLDOWN_NS = 500_000_000L
    private var lastBumpTime = 0L
    private var lastFallTime = 0L
    private var lastWheelieTime = 0L
    private var lastTiltTime = 0L

    var bumpCount = 0
        private set
    var maxMagnitude = 0f
        private set
    var elapsedMs = 0L
        private set
    private var startTimeMs = 0L

    private val timerHandler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            elapsedMs = System.currentTimeMillis() - startTimeMs
            onStateChanged?.invoke()
            timerHandler.postDelayed(this, 1000)
        }
    }

    companion object {
        const val CHANNEL_ID = "waytrace_rec"
        const val NOTIF_ID = 1
    }

    override fun onCreate() {
        super.onCreate()
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope     = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        val channel = NotificationChannel(CHANNEL_ID, "WayTrace Recording",
            NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    override fun onBind(intent: Intent): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int =
        START_STICKY

    private fun notification(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("WayTrace")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build()

    fun startRecording() {
        try {
            val now = Date()
            val ts = SimpleDateFormat("yyyyMMddHHmm", Locale.getDefault()).format(now)
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            dir.mkdirs()
            currentFile = File(dir, "WT_${ts}_CSV.csv")
            writer = PrintWriter(FileWriter(currentFile!!), true)
            writer!!.println("timestamp_ms,sensor,x,y,z,event")

            bumpCount = 0; maxMagnitude = 0f
            lastAccelTime = 0L; lastGyroTime = 0L
            startTimeMs = System.currentTimeMillis(); elapsedMs = 0L

            registerSensors()
            state = RecordingState.RECORDING
            startForeground(NOTIF_ID, notification("Recording"))
            timerHandler.post(timerRunnable)
            onStateChanged?.invoke()
        } catch (_: Exception) {}
    }

    fun pauseRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        elapsedMs = System.currentTimeMillis() - startTimeMs
        state = RecordingState.PAUSED
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, notification("Paused"))
        onStateChanged?.invoke()
    }

    fun resumeRecording() {
        startTimeMs = System.currentTimeMillis() - elapsedMs
        registerSensors()
        state = RecordingState.RECORDING
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, notification("Recording"))
        timerHandler.post(timerRunnable)
        onStateChanged?.invoke()
    }

    fun stopRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        if (state == RecordingState.RECORDING) elapsedMs = System.currentTimeMillis() - startTimeMs
        writer?.close(); writer = null
        state = RecordingState.STOPPED
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        onStateChanged?.invoke()
    }

    private fun registerSensors() {
        sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_GAME)
        sensorManager.registerListener(this, gyroscope,     SensorManager.SENSOR_DELAY_GAME)
    }

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
        // ISO 2631-5: clinically significant shock (~1g above 9.8 baseline)
        if (mag > 20.0f && nowNs - lastBumpTime > EVENT_COOLDOWN_NS) {
            lastBumpTime = nowNs; return "heavy_bump"
        }
        // ISO 2631-1: notable impact (~0.5g above baseline)
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

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) stopRecording()
    }
}
