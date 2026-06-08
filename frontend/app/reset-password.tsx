/**
 * /app/reset-password — landed via the Supabase password-recovery deep link.
 *
 * Flow:
 *  1. User taps "Forgot password?" on the login screen → receives an email
 *     with a recovery link (routr://reset-password on native, /reset-password
 *     on web).
 *  2. The link opens this screen. Supabase has already established a
 *     PASSWORD_RECOVERY session in the background via onAuthStateChange.
 *  3. User enters + confirms a new password → updatePassword() → navigate home.
 *
 * If the screen is opened without a valid recovery session (e.g. direct
 * navigation) it shows a gentle error and a back link.
 */
import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useSupabase } from '../src/contexts/SupabaseContext';

const COLOR = {
  bg0: '#04060a',
  bg1: '#0a0d14',
  text: '#F4F4F5',
  textDim: '#9CA3AF',
  textFaint: '#52525B',
  accent: '#FF5A00',
  hairline: 'rgba(255,255,255,0.08)',
  error: '#ef4444',
  success: '#10b981',
};

export default function ResetPasswordScreen() {
  const router = useRouter();
  const { user, updatePassword } = useSupabase();

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  // If there's no authenticated user when this screen mounts, the recovery
  // link hasn't been processed yet (or it expired). Show a clear message.
  const hasSession = !!user;

  const handleSubmit = async () => {
    setError('');
    if (newPassword.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    try {
      setLoading(true);
      const { error: updateErr } = await updatePassword(newPassword);
      if (updateErr) {
        setError(updateErr.message || 'Failed to update password. Please try again.');
        return;
      }
      setDone(true);
      Alert.alert(
        'Password updated',
        'Your password has been changed. You can now sign in.',
        [{ text: 'Continue', onPress: () => router.replace('/(tabs)') }],
      );
    } catch (e: any) {
      setError(e?.message || 'Unexpected error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (!hasSession) {
    return (
      <View style={styles.container}>
        <Pressable style={styles.backRow} onPress={() => router.replace('/')} hitSlop={8}>
          <Ionicons name="chevron-back" size={22} color={COLOR.accent} />
          <Text style={styles.backText}>Back to sign in</Text>
        </Pressable>
        <View style={styles.card}>
          <Ionicons name="alert-circle-outline" size={40} color={COLOR.textDim} />
          <Text style={styles.title}>Link expired</Text>
          <Text style={styles.subtitle}>
            This reset link has expired or was already used. Request a new one from the sign-in screen.
          </Text>
          <Pressable style={styles.button} onPress={() => router.replace('/')}>
            <Text style={styles.buttonText}>Back to sign in</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (done) {
    return (
      <View style={styles.container}>
        <View style={styles.card}>
          <Ionicons name="checkmark-circle-outline" size={48} color={COLOR.success} />
          <Text style={styles.title}>Password updated</Text>
          <Text style={styles.subtitle}>Redirecting you to the app…</Text>
          <ActivityIndicator color={COLOR.accent} style={{ marginTop: 16 }} />
        </View>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Pressable style={styles.backRow} onPress={() => router.replace('/')} hitSlop={8}>
        <Ionicons name="chevron-back" size={22} color={COLOR.accent} />
        <Text style={styles.backText}>Back to sign in</Text>
      </Pressable>

      <View style={styles.card}>
        <Ionicons name="lock-closed-outline" size={36} color={COLOR.accent} style={{ marginBottom: 8 }} />
        <Text style={styles.title}>Set a new password</Text>
        <Text style={styles.subtitle}>Choose a password at least 6 characters long.</Text>

        <View style={styles.inputGroup}>
          <View style={styles.passwordRow}>
            <TextInput
              value={newPassword}
              onChangeText={setNewPassword}
              placeholder="New password"
              placeholderTextColor={COLOR.textFaint}
              secureTextEntry={!showNew}
              autoCapitalize="none"
              autoCorrect={false}
              style={[styles.input, { flex: 1, marginBottom: 0 }]}
              testID="reset-new-password"
            />
            <Pressable
              onPress={() => setShowNew(v => !v)}
              hitSlop={8}
              style={styles.eyeButton}
              accessibilityLabel={showNew ? 'Hide password' : 'Show password'}
            >
              <Ionicons name={showNew ? 'eye-off-outline' : 'eye-outline'} size={20} color={COLOR.textDim} />
            </Pressable>
          </View>

          <View style={styles.passwordRow}>
            <TextInput
              value={confirmPassword}
              onChangeText={setConfirmPassword}
              placeholder="Confirm new password"
              placeholderTextColor={COLOR.textFaint}
              secureTextEntry={!showConfirm}
              autoCapitalize="none"
              autoCorrect={false}
              style={[styles.input, { flex: 1, marginBottom: 0 }]}
              testID="reset-confirm-password"
            />
            <Pressable
              onPress={() => setShowConfirm(v => !v)}
              hitSlop={8}
              style={styles.eyeButton}
              accessibilityLabel={showConfirm ? 'Hide confirm password' : 'Show confirm password'}
            >
              <Ionicons name={showConfirm ? 'eye-off-outline' : 'eye-outline'} size={20} color={COLOR.textDim} />
            </Pressable>
          </View>

          {!!error && (
            <Text style={styles.errorText}>{error}</Text>
          )}

          <Pressable
            style={[styles.button, loading && { opacity: 0.6 }]}
            onPress={handleSubmit}
            disabled={loading}
            testID="reset-submit"
          >
            {loading
              ? <ActivityIndicator color="#fff" />
              : <Text style={styles.buttonText}>Update password</Text>
            }
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLOR.bg0,
    paddingTop: 60,
    paddingHorizontal: 24,
  },
  backRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 32,
  },
  backText: {
    color: COLOR.accent,
    fontSize: 16,
    fontWeight: '600',
    marginLeft: 4,
  },
  card: {
    backgroundColor: COLOR.bg1,
    borderRadius: 18,
    padding: 28,
    borderWidth: 1,
    borderColor: COLOR.hairline,
    alignItems: 'center',
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    color: COLOR.text,
    textAlign: 'center',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    color: COLOR.textDim,
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: 24,
  },
  inputGroup: {
    width: '100%',
    gap: 12,
  },
  passwordRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  input: {
    backgroundColor: COLOR.bg0,
    borderWidth: 1,
    borderColor: COLOR.hairline,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: COLOR.text,
    fontSize: 14,
    letterSpacing: 0.4,
  },
  eyeButton: {
    padding: 10,
    borderWidth: 1,
    borderColor: COLOR.hairline,
    borderRadius: 10,
    backgroundColor: COLOR.bg0,
  },
  errorText: {
    color: COLOR.error,
    fontSize: 13,
    textAlign: 'center',
  },
  button: {
    backgroundColor: COLOR.accent,
    paddingVertical: 14,
    borderRadius: 14,
    alignItems: 'center',
    marginTop: 4,
  },
  buttonText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 15,
  },
});
