package com.vasil.sensorlogger

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.Uri
import android.os.Binder
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.MediaStore
import android.util.Log
import androidx.core.app.NotificationCompat
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.io.IOException
import java.io.OutputStreamWriter
import java.io.PrintWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

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
            .putString("current_file_uri", currentFileUri?.toString())
            .putString("current_file_name", currentFileName)
            .putLong("session_start_time", startTimeMs)
            .putLong("elapsed_ms", elapsedMs)
            .putInt("pinpoint_count", pinpointCount)
            .putString("start_display_time", startDisplayTime)
            .apply()
    }

    private fun clearState() { prefs.edit().clear().apply() }

    private fun restoreState(): Boolean {
        if (!prefs.getBoolean("is_active", false)) return false
        currentFileName  = prefs.getString("current_file_name", "") ?: ""
        startTimeMs      = prefs.getLong("session_start_time", 0L)
        elapsedMs        = prefs.getLong("elapsed_ms", 0L)
        pinpointCount    = prefs.getInt("pinpoint_count", 0)
        startDisplayTime = prefs.getString("start_display_time", "") ?: ""

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val uriStr = prefs.getString("current_file_uri", null)
                ?: run { clearState(); return false }
            currentFileUri = Uri.parse(uriStr)
            try {
                contentResolver.query(currentFileUri!!, arrayOf(MediaStore.Downloads.SIZE),
                    null, null, null)?.use { c ->
                    if (!c.moveToFirst()) { clearState(); return false }
                } ?: run { clearState(); return false }
            } catch (e: Exception) { clearState(); return false }
        } else {
            val path = prefs.getString("current_file", null)
                ?: run { clearState(); return false }
            currentFile = File(path)
            if (!currentFile!!.exists()) { clearState(); return false }
        }
        return true
    }

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    // v2 additions: optional sensors. null when the device lacks one.
    private var gravity:  Sensor? = null
    private var magnet:   Sensor? = null
    private var rotvec:   Sensor? = null
    private var pressure: Sensor? = null

    var state = RecordingState.READY
        private set

    private var writer: PrintWriter? = null
    var currentFile: File? = null
        private set
    private var currentFileUri: Uri? = null
    var currentFileName: String = ""
        private set

    fun getFileSize(): Long {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && currentFileUri != null) {
            try {
                contentResolver.query(currentFileUri!!, arrayOf(MediaStore.Downloads.SIZE),
                    null, null, null)?.use { c ->
                    if (c.moveToFirst()) c.getLong(0) else 0L
                } ?: 0L
            } catch (e: Exception) { 0L }
        } else {
            currentFile?.length() ?: 0L
        }
    }

    private val INTERVAL_NS = 8_333_333L  // 120 Hz
    private var lastAccelTime = 0L
    private var lastGyroTime = 0L

    // v2 high-rate throttle timestamps. pressure is naturally low-rate.
    private var lastGravityTime  = 0L
    private var lastMagTime      = 0L
    private var lastRotvecTime   = 0L

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
        // CHANNEL_ID is bumped (was "waytrace_rec") to force MIUI to re-create
        // the channel with HIGH importance. Notification-channel importance can
        // only be set on first creation — a renamed channel is the only way to
        // raise it after install. Without HIGH, MIUI throttles sensor delivery
        // to ~2 Hz after 60 seconds even with the wake lock held.
        const val CHANNEL_ID          = "waytrace_rec_v3_high"
        const val NOTIF_ID            = 1
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

        // v2 sensors — silently null on devices that lack a given sensor.
        gravity   = sensorManager.getDefaultSensor(Sensor.TYPE_GRAVITY)
        // Calibrated magnetometer — Android's fusion already subtracts hard/soft-iron
        // bias and returns three values that fit our CSV layout. The uncalibrated
        // variant returns six values (raw + bias), of which only three fit our schema.
        magnet    = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)
        rotvec    = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        pressure  = sensorManager.getDefaultSensor(Sensor.TYPE_PRESSURE)

        // IMPORTANCE_HIGH (was LOW) — required on MIUI to keep sensor
        // delivery at full rate beyond the first 60 seconds of recording.
        // Sound/vibration/lights disabled so the high-importance channel
        // doesn't pester the user with every notification update.
        val channel = NotificationChannel(CHANNEL_ID, "WayTrace Recording",
            NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Foreground service for continuous sensor recording"
            setSound(null, null)
            enableVibration(false)
            enableLights(false)
            setShowBadge(false)
        }
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
        val size = formatSize(getFileSize())
        val title = if (state == RecordingState.RECORDING || state == RecordingState.PAUSED)
            "$dur  $size" else "WayTrace"

        val pauseResumeIcon  = if (state == RecordingState.PAUSED)
            android.R.drawable.ic_media_play else android.R.drawable.ic_media_pause
        val pauseResumeLabel = if (state == RecordingState.PAUSED) "Resume" else "Pause"

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText("Started $startDisplayTime")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setSilent(true)                // no sound on per-second updates
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
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
        bytes < 1024        -> "${bytes}B"
        bytes < 1024 * 1024 -> "${"%.1f".format(bytes / 1024.0)}KB"
        else                -> "${"%.1f".format(bytes / (1024.0 * 1024))}MB"
    }

    // ── Recording ─────────────────────────────────────────────────────────────

    fun startRecording() {
        val now = Date()
        val ts  = SimpleDateFormat("yyyyMMddHHmm", Locale.getDefault()).format(now)
        startDisplayTime = SimpleDateFormat("HH:mm", Locale.getDefault()).format(now)
        val fileName = "ART-${ts}.csv"

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                // Android 10+: File API is blocked for shared storage — use MediaStore
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, fileName)
                    put(MediaStore.Downloads.MIME_TYPE, "text/csv")
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                }
                val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IOException("MediaStore insert returned null")
                currentFileUri = uri
                currentFile = null
                val os = contentResolver.openOutputStream(uri)
                    ?: throw IOException("MediaStore openOutputStream returned null")
                writer = PrintWriter(BufferedWriter(OutputStreamWriter(os)), true)
            } else {
                // Android 9 and below: legacy File API
                val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                dir.mkdirs()
                currentFile = File(dir, fileName)
                currentFileUri = null
                writer = PrintWriter(FileWriter(currentFile!!), true)
            }
            currentFileName = fileName
            writer!!.println("timestamp_ms,sensor,x,y,z,rotvec_w")
        } catch (e: Exception) {
            Log.e("WayTrace", "startRecording failed: ${e.message}", e)
            return
        }

        pinpointCount = 0
        lastAccelTime = 0L; lastGyroTime = 0L
        lastGravityTime = 0L; lastMagTime = 0L; lastRotvecTime = 0L
        startTimeMs = System.currentTimeMillis(); elapsedMs = 0L

        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WayTrace::Recording")
            .also { it.acquire(12 * 60 * 60 * 1000L) }

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
        if (writer == null) {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && currentFileUri != null) {
                    // "wa" = write-append mode
                    val os = contentResolver.openOutputStream(currentFileUri!!, "wa")
                        ?: throw IOException("Cannot reopen MediaStore stream for append")
                    writer = PrintWriter(BufferedWriter(OutputStreamWriter(os)), true)
                } else if (currentFile != null) {
                    writer = PrintWriter(FileWriter(currentFile!!, true), true)
                } else {
                    return
                }
            } catch (e: Exception) {
                Log.e("WayTrace", "resumeRecording failed: ${e.message}", e)
                return
            }
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
        // Pinpoint row format (v3): col 3 = N (the pinpoint counter); cols 4,5,6 = 0.
        writer?.println("$tsMs,pinpoint,$pinpointCount,0,0,")
        saveState()
        updateNotification()
        onStateChanged?.invoke()
    }

    private fun registerSensors() {
        sensorManager.registerListener(this, accelerometer, 8333)
        sensorManager.registerListener(this, gyroscope,     8333)

        // v2 high-rate IMU-class sensors at the same 8333 µs interval.
        gravity ?.let { sensorManager.registerListener(this, it, 8333) }
        magnet  ?.let { sensorManager.registerListener(this, it, 8333) }
        rotvec  ?.let { sensorManager.registerListener(this, it, 8333) }

        // Low-rate ambient sensor — default OS cadence is fine.
        pressure?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL) }
    }

    // ── Sensor events ─────────────────────────────────────────────────────────

    // v3: pure raw recording — no event detection in the app.
    // bump/heavy_bump/wheelie/tilt are computed offline by the Python tools.
    override fun onSensorChanged(event: SensorEvent) {
        if (state != RecordingState.RECORDING) return
        val nowNs = event.timestamp
        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                if (lastAccelTime != 0L && nowNs - lastAccelTime < INTERVAL_NS) return
                lastAccelTime = nowNs
                writer?.println("${nowNs / 1_000_000L},accel,${event.values[0]},${event.values[1]},${event.values[2]},")
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                writer?.println("${nowNs / 1_000_000L},gyro,${event.values[0]},${event.values[1]},${event.values[2]},")
            }
            Sensor.TYPE_GRAVITY -> {
                if (lastGravityTime != 0L && nowNs - lastGravityTime < INTERVAL_NS) return
                lastGravityTime = nowNs
                writer?.println("${nowNs / 1_000_000L},gravity,${event.values[0]},${event.values[1]},${event.values[2]},")
            }
            Sensor.TYPE_MAGNETIC_FIELD -> {
                if (lastMagTime != 0L && nowNs - lastMagTime < INTERVAL_NS) return
                lastMagTime = nowNs
                writer?.println("${nowNs / 1_000_000L},mag,${event.values[0]},${event.values[1]},${event.values[2]},")
            }
            Sensor.TYPE_ROTATION_VECTOR -> {
                if (lastRotvecTime != 0L && nowNs - lastRotvecTime < INTERVAL_NS) return
                lastRotvecTime = nowNs
                // ROTATION_VECTOR returns [x, y, z, w, (accuracy)]. Only rotvec
                // rows populate column 6 — the quaternion's W (rotvec_w).
                val w = if (event.values.size > 3) event.values[3] else 0f
                writer?.println("${nowNs / 1_000_000L},rotvec,${event.values[0]},${event.values[1]},${event.values[2]},$w")
            }
            Sensor.TYPE_PRESSURE -> {
                writer?.println("${nowNs / 1_000_000L},pressure,${event.values[0]},,,")
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        unregisterReceiver(actionReceiver)
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) stopRecording()
    }
}
