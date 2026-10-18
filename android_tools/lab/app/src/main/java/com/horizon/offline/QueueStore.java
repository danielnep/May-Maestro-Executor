package com.horizon.offline;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.*;

/** Single-owner durable outbox. Shared threads synchronize; independent processes must not share it.
 * Delivery is at-least-once: remote systems must deduplicate the immutable operation ID.
 */
public final class QueueStore {
    public enum Status { PENDING, INFLIGHT, ACKED }
    public static final class Entry {
        public final String id, payload;
        public final Status status;
        public final int attempts;
        public final long availableAt;
        Entry(String id, String payload, Status status, int attempts, long availableAt) {
            this.id=id; this.payload=payload; this.status=status; this.attempts=attempts; this.availableAt=availableAt;
        }
    }
    private final File file;
    private LinkedHashMap<String,Entry> entries = new LinkedHashMap<>();
    public QueueStore(File file) throws IOException {
        this.file=file;
        if (file.exists()) load();
        // A process death may leave a delivery unacknowledged. Retry with the same ID.
        boolean recovered=false;
        for (Entry e : new ArrayList<>(entries.values())) {
            if (e.status==Status.INFLIGHT) {
                entries.put(e.id,new Entry(e.id,e.payload,Status.PENDING,e.attempts,0));
                recovered=true;
            }
        }
        if (recovered) save();
    }
    public synchronized void enqueue(String id,String payload) throws IOException {
        if (id==null || id.isEmpty() || payload==null || id.length()>256 || payload.length()>16000)
            throw new IllegalArgumentException("Invalid operation");
        Entry existing=entries.get(id);
        if (existing!=null) {
            if (!existing.payload.equals(payload)) throw new IllegalArgumentException("Idempotency conflict");
            return;
        }
        replace(new Entry(id,payload,Status.PENDING,0,0));
    }
    public synchronized Entry acquire(long now) throws IOException {
        for (Entry e : entries.values()) {
            if (e.status==Status.PENDING && e.availableAt<=now) {
                Entry next=new Entry(e.id,e.payload,Status.INFLIGHT,e.attempts+1,e.availableAt);
                replace(next); return next;
            }
        }
        return null;
    }
    public synchronized void ack(String id) throws IOException {
        Entry e=require(id);
        if (e.status==Status.ACKED) return;
        if (e.status!=Status.INFLIGHT) throw new IllegalStateException("Operation was not acquired");
        replace(new Entry(e.id,e.payload,Status.ACKED,e.attempts,e.availableAt));
    }
    public synchronized void fail(String id,long now) throws IOException {
        Entry e=require(id);
        if (e.status!=Status.INFLIGHT) throw new IllegalStateException("Operation was not acquired");
        long delay=1000L << Math.min(10,Math.max(0,e.attempts-1));
        replace(new Entry(e.id,e.payload,Status.PENDING,e.attempts,Math.addExact(now,delay)));
    }
    public synchronized Entry get(String id) { return entries.get(id); }
    public synchronized int count(Status status) {
        int n=0; for (Entry e:entries.values()) if (e.status==status) n++; return n;
    }
    private Entry require(String id) {
        Entry e=entries.get(id); if (e==null) throw new IllegalArgumentException("Unknown operation"); return e;
    }
    private void replace(Entry value) throws IOException {
        Entry previous=entries.put(value.id,value);
        try { save(); }
        catch (IOException error) {
            if(previous==null) entries.remove(value.id); else entries.put(value.id,previous);
            throw error;
        }
    }
    private void save() throws IOException {
        File parent=file.getAbsoluteFile().getParentFile();
        if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("Cannot create storage");
        File temporary=new File(parent,file.getName()+".pending");
        try (FileOutputStream raw=new FileOutputStream(temporary);
             DataOutputStream out=new DataOutputStream(raw)) {
            out.writeInt(0x48514F31); out.writeInt(entries.size());
            for(Entry e:entries.values()) {
                out.writeUTF(e.id); out.writeUTF(e.payload); out.writeInt(e.status.ordinal());
                out.writeInt(e.attempts); out.writeLong(e.availableAt);
            }
            out.flush(); raw.getFD().sync();
        }
        Files.move(temporary.toPath(),file.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
    }
    private void load() throws IOException {
        try (DataInputStream in=new DataInputStream(new FileInputStream(file))) {
            if(in.readInt()!=0x48514F31) throw new IOException("Invalid outbox header");
            int count=in.readInt();
            if(count<0 || count>100000) throw new IOException("Invalid outbox size");
            for(int i=0;i<count;i++) {
                String id=in.readUTF(),payload=in.readUTF(); int state=in.readInt(),attempts=in.readInt(); long at=in.readLong();
                if(state<0 || state>=Status.values().length || attempts<0 || entries.containsKey(id))
                    throw new IOException("Invalid outbox record");
                entries.put(id,new Entry(id,payload,Status.values()[state],attempts,at));
            }
            if(in.read()!=-1) throw new IOException("Unexpected trailing bytes");
        }
    }
}
