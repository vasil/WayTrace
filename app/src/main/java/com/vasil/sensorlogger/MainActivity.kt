package com.vasil.sensorlogger

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.view.WindowManager
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity(), SensorEventListener {

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    private var isRecording = false
    private var writer: BufferedWriter? = null

    private val INTERVAL_NS = 100_000_000L  // 10 Hz
    private var lastAccelTime = 0L
    private var lastGyroTime = 0L

    private lateinit var btnToggle: Button
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        btnToggle = findViewById(R.id.btnToggle)
        tvStatus = findViewById(R.id.tvStatus)

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)

        btnToggle.setOnClickListener {
            if (isRecording) stopRecording() else startRecording()
        }
    }

    private fun startRecording() {
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
        val fileName = "sensors_$timestamp.csv"

        // App-private external storage — no permissions needed on any Android version
        val dir = getExternalFilesDir(null) ?: filesDir
        val file = File(dir, fileName)

        writer = BufferedWriter(FileWriter(file))
        writer!!.write("timestamp_ms,sensor,x,y,z\n")

        lastAccelTime = 0L
        lastGyroTime = 0L

        sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_FASTEST)
        sensorManager.registerListener(this, gyroscope, SensorManager.SENSOR_DELAY_FASTEST)

        isRecording = true
        btnToggle.text = "STOP"
        btnToggle.setBackgroundColor(0xFFCC0000.toInt())
        tvStatus.text = "Recording → $fileName\nLocation: Android/data/com.vasil.sensorlogger/files/"
    }

    private fun stopRecording() {
        sensorManager.unregisterListener(this)
        writer?.flush()
        writer?.close()
        writer = null

        isRecording = false
        btnToggle.text = "START"
        btnToggle.setBackgroundColor(0xFF007700.toInt())
        tvStatus.text = "Saved. Find file in:\nAndroid/data/com.vasil.sensorlogger/files/"
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (!isRecording) return
        val nowNs = event.timestamp

        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                if (lastAccelTime != 0L && nowNs - lastAccelTime < INTERVAL_NS) return
                lastAccelTime = nowNs
                writeLine(nowNs, "accel", event.values)
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                writeLine(nowNs, "gyro", event.values)
            }
        }
    }

    private fun writeLine(timestampNs: Long, sensor: String, v: FloatArray) {
        val ms = timestampNs / 1_000_000L
        try {
            writer?.write("$ms,$sensor,${v[0]},${v[1]},${v[2]}\n")
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        if (isRecording) stopRecording()
    }
}
