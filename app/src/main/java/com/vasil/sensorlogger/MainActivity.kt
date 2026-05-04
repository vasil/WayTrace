package com.vasil.sensorlogger

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
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
    private var currentFile: File? = null
    private val INTERVAL_NS = 100_000_000L
    private var lastAccelTime = 0L
    private var lastGyroTime = 0L
    private lateinit var btnToggle: Button
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
        try {
            val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
            val fileName = "sensors_$timestamp.csv"
            val dir = getExternalFilesDir(null)
            dir?.mkdirs()
            currentFile = File(dir, fileName)
            writer = BufferedWriter(FileWriter(currentFile!!))
            writer!!.write("timestamp_ms,sensor,x,y,z\n")
            lastAccelTime = 0L
            lastGyroTime = 0L
            val result1 = sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_GAME)
            val result2 = sensorManager.registerListener(this, gyroscope, SensorManager.SENSOR_DELAY_GAME)
            isRecording = true
            btnToggle.text = "STOP"
            btnToggle.setBackgroundColor(0xFFCC0000.toInt())
            tvStatus.text = "Recording: $fileName\nAccel:$result1 Gyro:$result2"
        } catch (e: Exception) {
            tvStatus.text = "Error: ${e.message}"
        }
    }

    private fun stopRecording() {
        sensorManager.unregisterListener(this)
        writer?.flush()
        writer?.close()
        writer = null
        isRecording = false
        btnToggle.text = "START"
        btnToggle.setBackgroundColor(0xFF007700.toInt())
        tvStatus.text = "Saved: ${currentFile?.absolutePath}"
    }

    override fun onSensorChanged(event: SensorEvent) {
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

    private fun writeLine(timestampNs: Long, sensor: String, values: FloatArray) {
        val ms = timestampNs / 1_000_000L
        try { writer?.write("$ms,$sensor,${values[0]},${values[1]},${values[2]}\n") }
        catch (e: Exception) { e.printStackTrace() }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        if (isRecording) stopRecording()
    }
}
