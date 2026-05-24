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
import android.os.HandlerThread
import android.os.PowerManager
import android.os.SystemClock
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
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

    var elapsedMs = 0L
        private set

    // ── OSI-019: live UDP streaming ───────────────────────────────────────
    // Off by default. Enabled from MainActivity once the user finds the
    // easter egg. Each sensor write is appended to a small buffer; when the
    // buffer reaches LIVE_BATCH_SIZE rows we fire one UDP packet on the
    // background thread. CSV write is unaffected.
    @Volatile var liveModeEnabled: Boolean = false
    @Volatile var liveTargetIp:    String  = "10.0.0.34"
    @Volatile var liveTargetPort:  Int     = 54321
    private var liveSocket:  DatagramSocket? = null
    private var liveAddress: InetAddress?    = null
    private var liveThread:  HandlerThread?  = null
    private var liveHandler: Handler?        = null
    private val liveBuffer:  MutableList<String> = ArrayList(64)
    private val LIVE_BATCH_SIZE = 30   // ~100 ms at 60 Hz × 5 sensor streams
    private val LIVE_HEADER     = "WTLIVE 1\n"
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

        // Sampling rate request — hardware caps to 60 Hz on this Xiaomi phone.
        const val SAMPLING_PERIOD_US            = 8333  // ~120 Hz request (OS caps to hw rate)
        // OSI-013: zero batching. Any non-zero maxReportLatencyUs combined
        // with high-rate listeners reproduces the 60 s sensor-death symptom.
        // The HIGH_SAMPLING_RATE_SENSORS permission is the other half of the fix.
        const val MAX_REPORT_LATENCY_US_OFFLINE = 0
        const val MAX_REPORT_LATENCY_US_LIVE    = 0
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

    // Prefer the wake-up variant of a sensor — MIUI's per-component scheduler
    // is not allowed to silence wake-up listeners (they're contractually
    // required to wake the AP). Falls back to the non-wake-up variant on
    // devices that don't expose a wake-up sensor for the requested type.
    private fun wakeupOrFallback(type: Int): Sensor? {
        return sensorManager.getDefaultSensor(type, true)
            ?: sensorManager.getDefaultSensor(type)
    }

    override fun onCreate() {
        super.onCreate()
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager

        // Wake-up variants — kernel contract prevents silencing while AP sleeps.
        accelerometer = wakeupOrFallback(Sensor.TYPE_ACCELEROMETER)
        gyroscope     = wakeupOrFallback(Sensor.TYPE_GYROSCOPE)

        // v2 sensors — silently null on devices that lack a given sensor.
        gravity   = wakeupOrFallback(Sensor.TYPE_GRAVITY)
        // Calibrated magnetometer — Android's fusion already subtracts hard/soft-iron
        // bias and returns three values that fit our CSV layout. The uncalibrated
        // variant returns six values (raw + bias), of which only three fit our schema.
        magnet    = wakeupOrFallback(Sensor.TYPE_MAGNETIC_FIELD)
        rotvec    = wakeupOrFallback(Sensor.TYPE_ROTATION_VECTOR)
        // Pressure rarely has a wake-up variant and we don't use it for bump
        // detection — regular variant is fine.
        pressure  = sensorManager.getDefaultSensor(Sensor.TYPE_PRESSURE)

        // Log hardware FIFO depth + wake-up flag. After this build, the
        // "WAKE" tag in logcat proves the wake-up variant was selected.
        // FIFO=0 means the framework will silently fall back to non-batched
        // delivery (no regression).
        fun fifo(label: String, s: Sensor?): String {
            if (s == null) return "$label=none"
            val wk = if (s.isWakeUpSensor) "WAKE" else "regular"
            return "$label=${s.fifoReservedEventCount}/${s.fifoMaxEventCount}/$wk"
        }
        Log.i("WayTrace", "sensor FIFO/wake (reserved/max/class): " +
            "${fifo("accel", accelerometer)}  ${fifo("gyro", gyroscope)}  " +
            "${fifo("gravity", gravity)}  ${fifo("magnet", magnet)}  " +
            "${fifo("rotvec", rotvec)}  ${fifo("pressure", pressure)}")

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
        val row = "$tsMs,pinpoint,$pinpointCount,0,0,"
        writer?.println(row); liveQueue(row)
        saveState()
        updateNotification()
        onStateChanged?.invoke()
    }

    private fun registerSensors() {
        // Batch latency depends on whether OSI-019 live streaming is running:
        // long batch (5 s) when only writing to CSV — saves wakeups, beats MIUI;
        // short batch (200 ms) when live-streaming so audio reacts in near-real-time.
        val maxLatency = if (liveModeEnabled) MAX_REPORT_LATENCY_US_LIVE
                         else                 MAX_REPORT_LATENCY_US_OFFLINE

        sensorManager.registerListener(this, accelerometer, SAMPLING_PERIOD_US, maxLatency)
        sensorManager.registerListener(this, gyroscope,     SAMPLING_PERIOD_US, maxLatency)

        // v2 high-rate IMU-class sensors at the same sampling interval.
        gravity ?.let { sensorManager.registerListener(this, it, SAMPLING_PERIOD_US, maxLatency) }
        magnet  ?.let { sensorManager.registerListener(this, it, SAMPLING_PERIOD_US, maxLatency) }
        rotvec  ?.let { sensorManager.registerListener(this, it, SAMPLING_PERIOD_US, maxLatency) }

        // Low-rate ambient sensor — default OS cadence is fine, but it can batch too.
        pressure?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL, maxLatency) }

        Log.i("WayTrace", "sensors registered: rate=${SAMPLING_PERIOD_US}µs " +
            "batch=${maxLatency}µs (live=$liveModeEnabled)")
    }

    // ── Sensor events ─────────────────────────────────────────────────────────

    // v3: pure raw recording — no event detection in the app.
    // bump/heavy_bump/wheelie/tilt are computed offline by the Python tools.
    override fun onSensorChanged(event: SensorEvent) {
        if (state != RecordingState.RECORDING) return
        val nowNs = event.timestamp
        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                val row = "${nowNs / 1_000_000L},accel,${event.values[0]},${event.values[1]},${event.values[2]},"
                writer?.println(row); liveQueue(row)
            }
            Sensor.TYPE_GYROSCOPE -> {
                val row = "${nowNs / 1_000_000L},gyro,${event.values[0]},${event.values[1]},${event.values[2]},"
                writer?.println(row); liveQueue(row)
            }
            Sensor.TYPE_GRAVITY -> {
                val row = "${nowNs / 1_000_000L},gravity,${event.values[0]},${event.values[1]},${event.values[2]},"
                writer?.println(row); liveQueue(row)
            }
            Sensor.TYPE_MAGNETIC_FIELD -> {
                val row = "${nowNs / 1_000_000L},mag,${event.values[0]},${event.values[1]},${event.values[2]},"
                writer?.println(row); liveQueue(row)
            }
            Sensor.TYPE_ROTATION_VECTOR -> {
                // ROTATION_VECTOR returns [x, y, z, w, (accuracy)]. Only rotvec
                // rows populate column 6 — the quaternion's W (rotvec_w).
                val w = if (event.values.size > 3) event.values[3] else 0f
                val row = "${nowNs / 1_000_000L},rotvec,${event.values[0]},${event.values[1]},${event.values[2]},$w"
                writer?.println(row); liveQueue(row)
            }
            Sensor.TYPE_PRESSURE -> {
                val row = "${nowNs / 1_000_000L},pressure,${event.values[0]},,,"
                writer?.println(row); liveQueue(row)
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    // ── OSI-019: live streaming control ───────────────────────────────────

    fun enableLiveMode(ip: String, port: Int) {
        liveTargetIp   = ip
        liveTargetPort = port
        if (liveThread == null) {
            liveThread = HandlerThread("WTLiveSender").also { it.start() }
            liveHandler = Handler(liveThread!!.looper)
        }
        liveHandler?.post {
            try {
                liveSocket  = DatagramSocket()
                liveAddress = InetAddress.getByName(ip)
                Log.i("WayTrace", "live mode ON -> $ip:$port")
            } catch (e: Exception) {
                Log.e("WayTrace", "live socket open failed: ${e.message}", e)
            }
        }
        liveModeEnabled = true
        // Drop sensor batch latency from 5 s to 0.2 s so live audio is responsive.
        if (state == RecordingState.RECORDING) registerSensors()
    }

    fun disableLiveMode() {
        liveModeEnabled = false
        liveHandler?.post {
            try { liveSocket?.close() } catch (_: Exception) {}
            liveSocket = null
            Log.i("WayTrace", "live mode OFF")
        }
        synchronized(liveBuffer) { liveBuffer.clear() }
        // Raise sensor batch latency back to 5 s — saves wakeups when only recording.
        if (state == RecordingState.RECORDING) registerSensors()
    }

    /** Append a row to the live buffer; flush as a UDP packet when full. */
    private fun liveQueue(row: String) {
        if (!liveModeEnabled) return
        val batch: List<String>?
        synchronized(liveBuffer) {
            liveBuffer.add(row)
            if (liveBuffer.size < LIVE_BATCH_SIZE) return
            batch = ArrayList(liveBuffer)
            liveBuffer.clear()
        }
        liveHandler?.post {
            val sock = liveSocket ?: return@post
            val addr = liveAddress ?: return@post
            try {
                val payload = (LIVE_HEADER + batch!!.joinToString("\n") + "\n")
                    .toByteArray(Charsets.US_ASCII)
                sock.send(DatagramPacket(payload, payload.size, addr, liveTargetPort))
            } catch (e: Exception) {
                // UDP send failed (laptop not reachable / WiFi flapping). Don't
                // crash; the next batch will try again. CSV write is unaffected.
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        unregisterReceiver(actionReceiver)
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) stopRecording()
        disableLiveMode()
        liveThread?.quitSafely(); liveThread = null
    }
}
