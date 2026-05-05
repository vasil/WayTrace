package com.vasil.sensorlogger

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.LocationManager
import android.os.Binder
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
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
    var pinpointCount = 0
        private set
    var startDisplayTime = ""
        private set
    private var startTimeMs = 0L

    private val timerHandler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            elapsedMs = System.currentTimeMillis() - startTimeMs
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
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int) = START_STICKY

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
        try {
            val now = Date()
            val ts  = SimpleDateFormat("yyyyMMddHHmm", Locale.getDefault()).format(now)
            startDisplayTime = SimpleDateFormat("HH:mm", Locale.getDefault()).format(now)

            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            dir.mkdirs()
            currentFile = File(dir, "WT_${ts}_CSV.csv")
            writer = PrintWriter(FileWriter(currentFile!!), true)
            writer!!.println("timestamp_ms,sensor,x,y,z,event,lat,lon")

            bumpCount = 0; maxMagnitude = 0f; pinpointCount = 0
            lastAccelTime = 0L; lastGyroTime = 0L
            startTimeMs = System.currentTimeMillis(); elapsedMs = 0L

            registerSensors()
            state = RecordingState.RECORDING
            startForeground(NOTIF_ID, buildNotification())
            timerHandler.post(timerRunnable)
            onStateChanged?.invoke()
        } catch (_: Exception) {}
    }

    fun pauseRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        elapsedMs = System.currentTimeMillis() - startTimeMs
        state = RecordingState.PAUSED
        updateNotification()
        onStateChanged?.invoke()
    }

    fun resumeRecording() {
        startTimeMs = System.currentTimeMillis() - elapsedMs
        registerSensors()
        state = RecordingState.RECORDING
        updateNotification()
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

    fun pinpoint() {
        if (state != RecordingState.RECORDING && state != RecordingState.PAUSED) return
        pinpointCount++
        val tsMs = SystemClock.elapsedRealtime()

        var lat = ""; var lon = ""
        try {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED) {
                val lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
                val loc = lm.getLastKnownLocation(LocationManager.GPS_PROVIDER)
                    ?: lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
                if (loc != null) {
                    lat = "%.7f".format(loc.latitude)
                    lon = "%.7f".format(loc.longitude)
                }
            }
        } catch (_: Exception) {}

        writer?.println("$tsMs,pinpoint,0.0,0.0,0.0,pinpoint_$pinpointCount,$lat,$lon")
        updateNotification()
        onStateChanged?.invoke()
    }

    private fun registerSensors() {
        sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_GAME)
        sensorManager.registerListener(this, gyroscope,     SensorManager.SENSOR_DELAY_GAME)
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
                writer?.println("${nowNs / 1_000_000L},accel,${event.values[0]},${event.values[1]},${event.values[2]},$ev,,")
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                val ev = detectGyroEvent(nowNs, event.values)
                writer?.println("${nowNs / 1_000_000L},gyro,${event.values[0]},${event.values[1]},${event.values[2]},$ev,,")
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
