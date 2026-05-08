package com.vasil.sensorlogger

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Binder
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
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

    private var wakeLock: PowerManager.WakeLock? = null

    private val prefs by lazy { getSharedPreferences("waytrace_state", Context.MODE_PRIVATE) }

    private fun saveState() {
        prefs.edit()
            .putBoolean("is_active", state == RecordingState.RECORDING || state == RecordingState.PAUSED)
            .putString("current_file", currentFile?.absolutePath)
            .putLong("session_start_time", startTimeMs)
            .putLong("elapsed_ms", elapsedMs)
            .putInt("bump_count", bumpCount)
            .putFloat("max_magnitude", maxMagnitude)
            .putInt("pinpoint_count", pinpointCount)
            .putString("start_display_time", startDisplayTime)
            .apply()
    }

    private fun clearState() { prefs.edit().clear().apply() }

    private fun restoreState(): Boolean {
        if (!prefs.getBoolean("is_active", false)) return false
        val path = prefs.getString("current_file", null) ?: return false
        currentFile = File(path)
        if (!currentFile!!.exists()) { clearState(); return false }
        startTimeMs      = prefs.getLong("session_start_time", 0L)
        elapsedMs        = prefs.getLong("elapsed_ms", 0L)
        bumpCount        = prefs.getInt("bump_count", 0)
        maxMagnitude     = prefs.getFloat("max_magnitude", 0f)
        pinpointCount    = prefs.getInt("pinpoint_count", 0)
        startDisplayTime = prefs.getString("start_display_time", "") ?: ""
        return true
    }

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    var state = RecordingState.READY
        private set

    private var writer: PrintWriter? = null
    var currentFile: File? = null
        private set

    private val INTERVAL_NS = 8_333_333L  // 120 Hz
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
    var pinpointCount = 0
        private set
    var startDisplayTime = ""
        private set
    private var startTimeMs = 0L


    private val timerHandler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            elapsedMs = System.currentTimeMillis() - startTimeMs
            saveState()
            updateNotification()
            onStateChanged?.invoke()
            timerHandler.postDelayed(this, 1000)
        }
    }

    companion object {
        const val CHANNEL_ID       = "waytrace_rec"
        const val NOTIF_ID         = 1
        const val ACTION_PAUSE_RESUME = "com.vasil.sensorlogger.PAUSE_RESUME"
        const val ACTION_PINPOINT     = "com.vasil.sensorlogger.PINPOINT"
        const val ACTION_STOP_REQUEST = "com.vasil.sensorlogger.STOP_REQUEST"
        const val EXTRA_STOP_DIALOG   = "show_stop_dialog"
    }

    private val actionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                ACTION_PAUSE_RESUME -> {
                    if (state == RecordingState.RECORDING) pauseRecording()
                    else if (state == RecordingState.PAUSED) resumeRecording()
                }
                ACTION_PINPOINT -> pinpoint()
                ACTION_STOP_REQUEST -> {
                    val i = Intent(context, MainActivity::class.java).apply {
                        flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                        putExtra(EXTRA_STOP_DIALOG, true)
                    }
                    startActivity(i)
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope     = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)

        val channel = NotificationChannel(CHANNEL_ID, "WayTrace Recording",
            NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)

        val filter = IntentFilter().apply {
            addAction(ACTION_PAUSE_RESUME)
            addAction(ACTION_PINPOINT)
            addAction(ACTION_STOP_REQUEST)
        }
        registerReceiver(actionReceiver, filter, RECEIVER_NOT_EXPORTED)
    }

    override fun onBind(intent: Intent): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (state == RecordingState.READY && restoreState()) {
            state = RecordingState.PAUSED
            try { startForeground(NOTIF_ID, buildNotification()) } catch (_: Exception) {}
            onStateChanged?.invoke()
        }
        return START_STICKY
    }

    // ── Notification ──────────────────────────────────────────────────────────

    private fun pendingBroadcast(action: String): PendingIntent =
        PendingIntent.getBroadcast(this, action.hashCode(),
            Intent(action), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun buildNotification(): Notification {
        val contentIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val s   = elapsedMs / 1000
        val dur = "%02d:%02d".format((s % 3600) / 60, s % 60)
        val size = formatSize(currentFile?.length() ?: 0L)
        val title = if (state == RecordingState.RECORDING || state == RecordingState.PAUSED)
            "$dur  $size" else "WayTrace"

        val pauseResumeIcon = if (state == RecordingState.PAUSED)
            android.R.drawable.ic_media_play else android.R.drawable.ic_media_pause
        val pauseResumeLabel = if (state == RecordingState.PAUSED) "Resume" else "Pause"

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText("Started $startDisplayTime")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .addAction(pauseResumeIcon, pauseResumeLabel,
                pendingBroadcast(ACTION_PAUSE_RESUME))
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop",
                pendingBroadcast(ACTION_STOP_REQUEST))
            .addAction(android.R.drawable.ic_menu_mylocation, "Pin $pinpointCount",
                pendingBroadcast(ACTION_PINPOINT))
            .build()
    }

    private fun updateNotification() {
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) {
            getSystemService(NotificationManager::class.java)
                .notify(NOTIF_ID, buildNotification())
        }
    }

    private fun formatSize(bytes: Long): String = when {
        bytes < 1024          -> "${bytes}B"
        bytes < 1024 * 1024   -> "${"%.1f".format(bytes / 1024.0)}KB"
        else                   -> "${"%.1f".format(bytes / (1024.0 * 1024))}MB"
    }

    // ── Recording ─────────────────────────────────────────────────────────────

    fun startRecording() {
        val now = Date()
        val ts  = SimpleDateFormat("yyyyMMddHHmm", Locale.getDefault()).format(now)
        startDisplayTime = SimpleDateFormat("HH:mm", Locale.getDefault()).format(now)

        try {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            dir.mkdirs()
            currentFile = File(dir, "ART-${ts}.csv")
            writer = PrintWriter(FileWriter(currentFile!!), true)
            writer!!.println("timestamp_ms,sensor,x,y,z,event")
        } catch (_: Exception) { return }

        bumpCount = 0; maxMagnitude = 0f; pinpointCount = 0
        lastAccelTime = 0L; lastGyroTime = 0L
        startTimeMs = System.currentTimeMillis(); elapsedMs = 0L

        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WayTrace::Recording")
            .also { it.acquire(12 * 60 * 60 * 1000L) } // 12-hour safety cap

        registerSensors()
        state = RecordingState.RECORDING
        saveState()
        onStateChanged?.invoke()
        try { startForeground(NOTIF_ID, buildNotification()) } catch (_: Exception) {}
        timerHandler.post(timerRunnable)
    }

    fun pauseRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        elapsedMs = System.currentTimeMillis() - startTimeMs
        wakeLock?.release(); wakeLock = null
        state = RecordingState.PAUSED
        saveState()
        updateNotification()
        onStateChanged?.invoke()
    }

    fun resumeRecording() {
        if (writer == null && currentFile != null) {
            try { writer = PrintWriter(FileWriter(currentFile!!, true), true) } catch (_: Exception) { return }
        }
        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WayTrace::Recording")
            .also { it.acquire(12 * 60 * 60 * 1000L) }

        startTimeMs = System.currentTimeMillis() - elapsedMs
        registerSensors()
        state = RecordingState.RECORDING
        saveState()
        updateNotification()
        timerHandler.post(timerRunnable)
        onStateChanged?.invoke()
    }

    fun stopRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        if (state == RecordingState.RECORDING) elapsedMs = System.currentTimeMillis() - startTimeMs
        wakeLock?.release(); wakeLock = null
        writer?.close(); writer = null
        clearState()
        state = RecordingState.STOPPED
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        onStateChanged?.invoke()
    }

    fun pinpoint() {
        if (state != RecordingState.RECORDING && state != RecordingState.PAUSED) return
        pinpointCount++
        val tsMs = SystemClock.elapsedRealtime()
        writer?.println("$tsMs,pinpoint,0.0,0.0,0.0,pinpoint_$pinpointCount")
        saveState()
        updateNotification()
        onStateChanged?.invoke()
    }

    private fun registerSensors() {
        // 8333 µs = 120 Hz
        sensorManager.registerListener(this, accelerometer, 8333)
        sensorManager.registerListener(this, gyroscope,     8333)
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
                writer?.println("${nowNs / 1_000_000L},accel,${event.values[0]},${event.values[1]},${event.values[2]},$ev")
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                val ev = detectGyroEvent(nowNs, event.values)
                writer?.println("${nowNs / 1_000_000L},gyro,${event.values[0]},${event.values[1]},${event.values[2]},$ev")
            }
        }
    }

    private fun detectAccelEvent(nowNs: Long, v: FloatArray, mag: Float): String {
        if (mag > 20.0f && nowNs - lastBumpTime > EVENT_COOLDOWN_NS) {
            lastBumpTime = nowNs; return "heavy_bump"
        }
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

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        unregisterReceiver(actionReceiver)
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) stopRecording()
    }
}
